"""Операторски път за позиция, чиято греда не е доказана.

ЗАЩО СЪЩЕСТВУВА
---------------
Същият случай като при рамките: ``record_beam_decision`` работи и е тестван,
но без UI Финалният контрол просто блокираше експорта и подканваше „отстрани
посочената техническа причина", без да дава с какво. Реален случай -- проект
``s-9``, осем позиции в гредовия чертеж.

ЗАЩО ИЗБОРЪТ Е МЕЖДУ ВСИЧКИ ГРЕДИ
---------------------------------
Изкушаващо е да се предложи най-близката греда. Не се прави: при тези осем
позиции собственикът провери подредените по близост предложения и отсече
„никое не е вярно" (2026-09-13). Затова списъкът е пълен и подреден по име,
а разстоянието се показва само като справка, не като препоръка.

ЗАЩО ТОЗИ ДИАЛОГ НЕ Е ГЛАВНИЯТ ПЪТ
----------------------------------
Собственик, 2026-09-13, като го видя: „и как да ги посоча?"

Списъкът дава 26 падащи менюта и нито един ориентир КЪДЕ на чертежа е всяка
позиция. Разумният ход е обратният: да се види чертежът и да се огради
гредата заедно с позициите ѝ (``export/operator_areas.py``). Затова тук стои
трети бутон, който затваря диалога и подкарва точно това. Изборът от списък
остава за единични случаи, когато операторът вече знае отговора.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

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

from lion_rebar.export.beam_decisions import (
    BeamDecisionError,
    record_beam_decision,
)
from lion_rebar.project.data_identity import DataIdentity


class UnresolvedBeamDialog(QDialog):
    """Операторът посочва гредата на позиция, която автоматиката не доказа."""

    def __init__(
        self,
        positions: Sequence[tuple[str, str]],
        beam_titles: Sequence[tuple[str, str]],
        project_root: Path,
        identity: DataIdentity,
        operator: str,
        parent=None,
    ):
        """``positions`` са (handle, текст на надписа); ``beam_titles`` са
        (handle на заглавието, заглавие) за ВСИЧКИ греди в чертежа."""

        super().__init__(parent)
        self.setWindowTitle("LION REBAR — Позиции без доказана греда")
        self.resize(1000, 420)
        self._positions = list(positions)
        self._beam_titles = sorted(beam_titles, key=lambda item: item[1])
        self._known = {handle: title for handle, title in self._beam_titles}
        self._project_root = Path(project_root)
        self._identity = identity
        self._operator = str(operator or "").strip() or "оператор"
        self._beam_boxes: list[QComboBox] = []
        self._note_edits: list[QLineEdit] = []
        self.decisions_made = 0
        #: Операторът е избрал да ги огради на чертежа вместо да ги изброява.
        self.wants_drawing_overview = False

        layout = QVBoxLayout(self)
        intro = QLabel(
            "За тези позиции нито едно заглавие на греда не попада в приетия "
            "прозорец, затова гредата им не е доказана и Финалният контрол "
            "спира експорта. Посочи гредата на всяка и опиши защо.\n\n"
            "Списъкът е пълен и подреден по име — нарочно не се предлага "
            "най-близката греда, защото при този тип чертеж близостта се "
            "оказа подвеждаща. Решението се записва с автор и време, "
            "обезсилва Финалния контрол и остава в одитната следа. "
            "Позиция без избор остава нерешена.\n\n"
            "Ако не знаеш коя греда е коя — това е нормално, списъкът не "
            "показва къде са. Натисни „Покажи ги на чертежа“: позициите "
            "излизат в червено, заглавията на гредите в синьо, и всяка греда "
            "се огражда наведнъж с два клика."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(len(self._positions), 3, self)
        self.table.setHorizontalHeaderLabels(
            ["Позиция", "Греда", "Обосновка"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        for row, (handle, text) in enumerate(self._positions):
            item = QTableWidgetItem(f"{text}  ({handle})")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item)

            combo = QComboBox(self.table)
            combo.addItem("— не решавай сега —", "")
            for label_handle, title in self._beam_titles:
                combo.addItem(title, label_handle)
            self.table.setCellWidget(row, 1, combo)
            self._beam_boxes.append(combo)

            note = QLineEdit(self.table)
            note.setPlaceholderText("Задължително при избрана греда")
            self.table.setCellWidget(row, 2, note)
            self._note_edits.append(note)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Запиши решенията"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Откажи")
        # Главният път е този: виж чертежа и огради гредата с позициите ѝ.
        показване = buttons.addButton(
            "Покажи ги на чертежа",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        показване.setToolTip(
            "Затваря списъка и оцветява позициите и гредите върху чертежа, "
            "за да се ограждат с два клика."
        )
        показване.clicked.connect(self._show_on_drawing)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _show_on_drawing(self) -> None:
        """Затваря списъка и оставя главния прозорец да ги покаже."""

        self.wants_drawing_overview = True
        self.reject()

    def _save(self) -> None:
        pending: list[tuple[str, str, str, str]] = []
        for (handle, text), combo, note in zip(
            self._positions, self._beam_boxes, self._note_edits
        ):
            label_handle = str(combo.currentData() or "")
            if not label_handle:
                continue
            reason = note.text().strip()
            if not reason:
                QMessageBox.warning(
                    self,
                    "Липсва обосновка",
                    f"Позиция „{text}“ има избрана греда, но няма обосновка.\n\n"
                    "Решение за производствена принадлежност изисква мотив.",
                )
                return
            pending.append((handle, text, label_handle, reason))

        if not pending:
            QMessageBox.information(
                self, "Няма решения", "Не е избрана греда за нито една позиция."
            )
            return

        saved = 0
        try:
            for handle, _text, label_handle, reason in pending:
                record_beam_decision(
                    self._project_root,
                    annotation_handle=handle,
                    beam_label_handle=label_handle,
                    decided_by=self._operator,
                    note=reason,
                    identity=self._identity,
                    assignment_is_unresolved=True,
                    known_beam_handles=self._known,
                )
                saved += 1
        except BeamDecisionError as error:
            QMessageBox.critical(
                self,
                "Решението не беше записано",
                f"Записани са {saved} от {len(pending)}.\n\n{error}",
            )
            self.decisions_made = saved
            return

        self.decisions_made = saved
        QMessageBox.information(
            self,
            "Записано",
            f"Записани са {saved} решения за греди.\n\n"
            "Финалният контрол е обезсилен — пусни го наново преди export.",
        )
        self.accept()
