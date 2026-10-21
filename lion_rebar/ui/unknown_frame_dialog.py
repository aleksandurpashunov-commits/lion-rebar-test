"""Операторски път за рамки с неопределена категория.

ЗАЩО СЪЩЕСТВУВА
---------------
``record_frame_decision`` работеше и беше тестван, но нямаше UI. Рамка с
категория ``UNKNOWN`` блокираше автоматичния Excel и операторът нямаше
откъде да реши какво е тя — реален случай:
„АРМИРОВКА ВЕРТИКАЛИ ДО КОТА +8,90“.

Backend без операторски път не е завършена функция.
"""
from __future__ import annotations

from pathlib import Path

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

from lion_rebar.export.frame_decisions import (
    SELECTABLE_CATEGORIES,
    FrameDecisionError,
    record_frame_decision,
)
from lion_rebar.export.frame_detection import CATEGORY_SHEET_NAMES, DrawingFrame
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.session.operator_identity import НЕЗАЯВЕН


class UnknownFrameDialog(QDialog):
    """Позволява на оператора да определи категорията на UNKNOWN рамки."""

    def __init__(self, frames: list[DrawingFrame], project_root: Path,
                 identity: DataIdentity, operator: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LION REBAR — Рамки с неопределена категория")
        self.resize(980, 420)
        self._frames = list(frames)
        self._project_root = Path(project_root)
        self._identity = identity
        self._operator = str(operator or "").strip() or НЕЗАЯВЕН
        self._category_boxes: list[QComboBox] = []
        self._note_edits: list[QLineEdit] = []
        self.decisions_made = 0

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Тези рамки не са разпознати автоматично и блокират автоматичния "
            "Excel. Определи категорията на всяка и опиши защо.\n\n"
            "Решението се записва с автор и време, обезсилва Финалния контрол "
            "и остава в одитната следа. Рамка без избор остава неопределена."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(len(self._frames), 4, self)
        self.table.setHorizontalHeaderLabels(
            ["Заглавие в чертежа", "Разпознато", "Категория", "Обосновка"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        for row, frame in enumerate(self._frames):
            title = QTableWidgetItem(frame.title_text or "—")
            title.setFlags(title.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, title)

            detected = QTableWidgetItem(
                f"{frame.category_code} ({frame.confidence})")
            detected.setFlags(detected.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, detected)

            combo = QComboBox(self.table)
            combo.addItem("— не решавай сега —", "")
            for code in SELECTABLE_CATEGORIES:
                combo.addItem(CATEGORY_SHEET_NAMES.get(code, code), code)
            self.table.setCellWidget(row, 2, combo)
            self._category_boxes.append(combo)

            note = QLineEdit(self.table)
            note.setPlaceholderText("Задължително при избрана категория")
            self.table.setCellWidget(row, 3, note)
            self._note_edits.append(note)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Запиши решенията")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Откажи")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        pending: list[tuple[DrawingFrame, str, str]] = []
        for frame, combo, note in zip(self._frames, self._category_boxes,
                                      self._note_edits):
            code = str(combo.currentData() or "")
            if not code:
                continue
            text = note.text().strip()
            if not text:
                QMessageBox.warning(
                    self, "Липсва обосновка",
                    f"Рамка „{frame.title_text or frame.marker_handle}“ има "
                    "избрана категория, но няма обосновка.\n\n"
                    "Решение за производствена категория изисква мотив.")
                return
            pending.append((frame, code, text))

        if not pending:
            QMessageBox.information(
                self, "Няма решения",
                "Не е избрана категория за нито една рамка.")
            return

        saved = 0
        try:
            for frame, code, text in pending:
                record_frame_decision(
                    self._project_root,
                    marker_handle=frame.marker_handle,
                    category_code=code,
                    decided_by=self._operator,
                    note=text,
                    identity=self._identity,
                    frame_is_unknown=True,
                )
                saved += 1
        except FrameDecisionError as error:
            QMessageBox.critical(
                self, "Решението не беше записано",
                f"Записани са {saved} от {len(pending)}.\n\n{error}")
            self.decisions_made = saved
            return

        self.decisions_made = saved
        QMessageBox.information(
            self, "Записано",
            f"Записани са {saved} решения за рамки.\n\n"
            "Финалният контрол е обезсилен — пусни го наново преди export.")
        self.accept()
