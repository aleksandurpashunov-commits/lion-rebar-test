"""Операторски път за групово прилагане на решение.

ЗАЩО СЪЩЕСТВУВА
---------------
Backend-ът (``review/apply_to_group.py``) прилага транзакционно и връща
реално, но нямаше нито един UI извикващ. Независим одит го отбеляза
двукратно: „код без production caller не е завършена функция“.

ПРИНЦИП
-------
Операторът вижда ТОЧНО кои позиции ще бъдат засегнати и кои са изключени
и защо — ПРЕДИ да потвърди. Отказът е толкова лесен, колкото приемането.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.review.apply_to_group import (
    GroupApplicationError,
    apply_group_plan,
    build_group_plan,
)
from lion_rebar.session.operator_identity import НЕЗАЯВЕН


class ApplyToGroupDialog(QDialog):
    """Преглед и потвърждение на групово решение."""

    def __init__(self, representative: dict, rows: list[dict],
                 changes: dict, project_root: Path, identity: DataIdentity,
                 operator: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LION REBAR — Приложи към групата")
        self.resize(1040, 520)
        self._representative = representative
        self._rows_by_handle = {
            str(row.get("annotation_handle")): row for row in rows
        }
        self._project_root = Path(project_root)
        self._identity = identity
        self._operator = str(operator or "").strip() or НЕЗАЯВЕН
        self.operation_id: str | None = None

        self._plan = build_group_plan(representative, rows, identity,
                                      changes=changes)

        layout = QVBoxLayout(self)

        header = QLabel(
            f"<b>Представител:</b> "
            f"{representative.get('annotation_text') or '—'}<br>"
            f"<b>Промени:</b> "
            + ", ".join(f"{key} = {value}"
                        for key, value in (self._plan.changes or {}).items())
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(header)

        summary = QLabel(
            f"Ще бъдат засегнати <b>{self._plan.affected_count}</b> позиции. "
            f"Изключени: {len(self._plan.excluded)}."
        )
        summary.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(summary)

        members = list(self._plan.eligible) + list(self._plan.excluded)
        self.table = QTableWidget(len(members), 4, self)
        self.table.setHorizontalHeaderLabels(
            ["Надпис", "Handle", "Ще се приложи", "Причина за изключване"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        for row_index, member in enumerate(members):
            for column, value in enumerate((
                member.annotation_text or "—",
                member.annotation_handle,
                "ДА" if member.eligible else "не",
                member.reason or "",
            )):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if not member.eligible:
                    item.setForeground(Qt.GlobalColor.darkGray)
                self.table.setItem(row_index, column, item)
        layout.addWidget(self.table)

        layout.addWidget(QLabel("Инженерна бележка (задължителна):"))
        self.note = QPlainTextEdit(self)
        self.note.setPlaceholderText(
            "Защо тези позиции получават едно и също решение.")
        self.note.setMaximumHeight(80)
        layout.addWidget(self.note)

        if self._plan.refusal:
            refusal = QLabel(
                f"<b style='color:#991b1b'>Действието е отказано: "
                f"{self._plan.refusal}</b>")
            refusal.setTextFormat(Qt.TextFormat.RichText)
            refusal.setWordWrap(True)
            layout.addWidget(refusal)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self)
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_button.setText(
            f"Приложи към {self._plan.affected_count} позиции")
        apply_button.setEnabled(self._plan.can_apply)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Откажи")
        apply_button.clicked.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply(self) -> None:
        note = self.note.toPlainText().strip()
        if not note:
            QMessageBox.warning(
                self, "Липсва бележка",
                "Груповото решение изисква инженерна бележка.")
            return
        try:
            self.operation_id = apply_group_plan(
                self._project_root, self._plan, self._rows_by_handle,
                self._identity, applied_by=self._operator, note=note)
        except GroupApplicationError as error:
            QMessageBox.critical(
                self, "Груповото решение не беше приложено",
                f"{error}\n\nНито една позиция не е променена.")
            return
        QMessageBox.information(
            self, "Приложено",
            f"Приложено към {self._plan.affected_count} позиции.\n"
            f"Операция: {self.operation_id}\n\n"
            "Финалният контрол е обезсилен — пусни го наново преди export.\n"
            "Действието може да бъде върнато, докато не е направен export.")
        self.accept()
