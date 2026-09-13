from __future__ import annotations

from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lion_rebar.export.excel_export import ExportFrameConfig, ExportPosition
from lion_rebar.export.frame_detection import (
    CATEGORY_ORDER,
    CATEGORY_SHEET_NAMES,
)
from lion_rebar.export.frame_detection import CATEGORY_SHEET_NAMES, DrawingFrame, sanitize_sheet_name


class ExportExcelDialog(QDialog):
    #: Категориите се извеждат от ЕДИН източник -- frame_detection. Дублиран
    #: списък тук означава, че нова категория може да липсва в диалога и
    #: рамката тихо да получи първата опция.
    #:
    #: Реален дефект (независим одит на rc21): VERTICALS липсваше и рамка
    #: „вертикали“ се експортираше като „долна“, докато manifest-ът
    #: записваше VERTICALS и връщаше PRODUCTION VERIFIED.
    CATEGORY_OPTIONS = [
        (code, CATEGORY_SHEET_NAMES[code])
        for code, _order in sorted(CATEGORY_ORDER.items(), key=lambda item: item[1])
        if code != "UNKNOWN"
    ]

    def __init__(self, frames: list[DrawingFrame], positions: list[ExportPosition], parent=None):
        super().__init__(parent)
        self.setWindowTitle("LION REBAR — Експорт Excel + DXF")
        self.resize(1120, 520)
        self._frames = list(frames)
        counts = Counter(position.frame_index for position in positions)

        layout = QVBoxLayout(self)
        summary = QLabel(
            f"Намерени чертежи: {len(frames)} | Безопасни позиции за export: {len(positions)}\n"
            "Провери предложената категория и името на всеки лист. Export-ът не включва "
            "MISSING_MASTER, NO_VALID_CANDIDATE или други изключени позиции. "
            "Същите номера ще бъдат записани и в копие на DXF като кръгли BLOCK позиции."
        )
        summary.setWordWrap(True)
        summary.setStyleSheet("background:#eff6ff; border:1px solid #93c5fd; padding:10px; color:#1e3a8a;")
        layout.addWidget(summary)

        self.table = QTableWidget(len(frames), 6, self)
        self.table.setHorizontalHeaderLabels(
            ["№", "Текст от ЧЕРТЕЖ:", "Подсказка", "Категория", "Име на лист", "Позиции"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._category_boxes: list[QComboBox] = []
        self._name_edits: list[QLineEdit] = []
        for row, frame in enumerate(frames):
            number = QTableWidgetItem(str(frame.index))
            number.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            number.setFlags(number.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, number)

            title = QTableWidgetItem(frame.title_text or "—")
            title.setFlags(title.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, title)
            support = QTableWidgetItem(frame.supporting_text or "—")
            support.setFlags(support.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, support)

            combo = QComboBox(self.table)
            selected_index = None
            for index, (code, label) in enumerate(self.CATEGORY_OPTIONS):
                combo.addItem(label, code)
                if code == frame.category_code:
                    selected_index = index
            if selected_index is None:
                # Непозната категория НЕ се заменя мълчаливо с първата опция.
                # Добавя се изрично, за да не се промени изходът без знанието
                # на оператора.
                combo.addItem(
                    f"{frame.suggested_sheet_name or frame.category_code} "
                    "(непозната категория)",
                    frame.category_code,
                )
                selected_index = combo.count() - 1
            combo.setCurrentIndex(selected_index)
            self.table.setCellWidget(row, 3, combo)
            self._category_boxes.append(combo)

            edit = QLineEdit(frame.suggested_sheet_name, self.table)
            self.table.setCellWidget(row, 4, edit)
            self._name_edits.append(edit)
            combo.currentIndexChanged.connect(
                lambda _value, edit=edit, combo=combo: self._category_changed(edit, combo)
            )

            count = QTableWidgetItem(str(counts.get(frame.index, 0)))
            count.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            count.setFlags(count.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 5, count)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        note = QLabel(
            "Колона ВИД се генерира от заключената MASTER форма. Ширината на G и височината "
            "на редовете се настройват автоматично така, че изображението да остава изцяло "
            "в клетката и да не пресича съседните колони."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#374151; padding:4px;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Генерирай Excel + DXF")
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def configs(self) -> list[ExportFrameConfig]:
        result: list[ExportFrameConfig] = []
        for frame, combo, edit in zip(self._frames, self._category_boxes, self._name_edits):
            result.append(
                ExportFrameConfig(
                    frame_index=frame.index,
                    category_code=str(combo.currentData()),
                    sheet_name=sanitize_sheet_name(edit.text(), fallback=f"лист {frame.index}"),
                    title_text=frame.combined_title,
                    confirmed=True,
                )
            )
        return result

    def _category_changed(self, edit: QLineEdit, combo: QComboBox) -> None:
        code = str(combo.currentData())
        edit.setText(CATEGORY_SHEET_NAMES.get(code, combo.currentText()))

    def _accept_checked(self) -> None:
        configs = self.configs()
        names = [config.sheet_name.lower() for config in configs]
        if len(set(names)) != len(names):
            QMessageBox.warning(self, "Дублирани листове", "Имената на листовете трябва да са уникални.")
            return
        if any(not config.sheet_name for config in configs):
            QMessageBox.warning(self, "Липсва име", "Всеки чертеж трябва да има име на лист.")
            return
        self.accept()
