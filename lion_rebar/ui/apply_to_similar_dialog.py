"""Операторски път за "Приложи към всички подобни".

ЗАЩО СЪЩЕСТВУВА
---------------
Backend-ът (``review/apply_to_similar.py``) прилага транзакционно и връща
реално, но нямаше нито един UI извикващ — същият клас дефект, който
независимият одит намери за "Приложи към групата" преди Фаза 0.

ПРИНЦИП
-------
Операторът вижда ТОЧНО кои позиции ще бъдат засегнати и кои са изключени и
защо — ПРЕДИ да потвърди. Функцията е LEVEL 2 (изключена по подразбиране,
``review/apply_to_similar.py::FEATURE_ENABLED_DEFAULT``) — диалогът показва
пълния истински преглед дори докато е изключена, но бутонът "Приложи" остава
неактивен, докато собственикът съзнателно не я включи.
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
from lion_rebar.review.apply_to_similar import (
    FEATURE_ENABLED_DEFAULT,
    SimilarApplicationError,
    apply_similar_plan,
    build_similar_plan,
)
from lion_rebar.session.operator_identity import НЕЗАЯВЕН


class ApplyToSimilarDialog(QDialog):
    """Преглед и потвърждение на "Приложи към всички подобни"."""

    def __init__(self, representative: dict, all_rows: list[dict],
                 changes: dict, project_root: Path, identity: DataIdentity,
                 operator: str, parent=None, *, enabled: bool | None = None):
        super().__init__(parent)
        self.setWindowTitle("LION REBAR — Приложи към всички подобни")
        self.resize(1080, 560)
        self._representative = representative
        self._rows_by_handle = {
            str(row.get("annotation_handle")): row for row in all_rows
        }
        self._project_root = Path(project_root)
        self._identity = identity
        self._operator = str(operator or "").strip() or НЕЗАЯВЕН
        self.operation_id: str | None = None

        # ``enabled`` е None в production — тогава важи module-level
        # FEATURE_ENABLED_DEFAULT (LEVEL 2, изключена, докато собственикът
        # съзнателно не я включи). Явен bool (напр. от тестове, или бъдеща
        # настройка в проекта) го замества.
        resolved_enabled = FEATURE_ENABLED_DEFAULT if enabled is None else bool(enabled)
        self._plan = build_similar_plan(representative, all_rows, identity,
                                        changes=changes, enabled=resolved_enabled)

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

        if not self._plan.enabled:
            disabled_notice = QLabel(
                "<b style='color:#991b1b'>Функцията е ИЗКЛЮЧЕНА (LEVEL 2).</b> "
                "Прегледът по-долу е реален — показва точно какво БИ станало — "
                "но прилагането е блокирано, докато не бъде съзнателно включена."
            )
            disabled_notice.setTextFormat(Qt.TextFormat.RichText)
            disabled_notice.setWordWrap(True)
            layout.addWidget(disabled_notice)

        summary = QLabel(
            f"Ще бъдат засегнати <b>{self._plan.affected_count}</b> позиции "
            f"в <b>{self._plan.affected_groups}</b> групи за преглед. "
            f"Изключени: {len(self._plan.excluded)}."
        )
        summary.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(summary)

        members = list(self._plan.included) + list(self._plan.excluded)
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
                "ДА" if member.included else "не",
                member.reason or "",
            )):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if not member.included:
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
                "Действието изисква инженерна бележка.")
            return
        try:
            self.operation_id = apply_similar_plan(
                self._project_root, self._plan, self._rows_by_handle,
                self._identity, applied_by=self._operator, note=note)
        except SimilarApplicationError as error:
            QMessageBox.critical(
                self, "Действието не беше приложено",
                f"{error}\n\nНито една позиция не е променена.")
            return
        QMessageBox.information(
            self, "Приложено",
            f"Приложено към {self._plan.affected_count} позиции.\n"
            f"Операция: {self.operation_id}\n\n"
            "Финалният контрол е обезсилен — пусни го наново преди export.\n"
            "Действието може да бъде върнато, докато не е направен export.")
        self.accept()
