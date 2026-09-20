"""Паспорт на чертежа — екранът, на който операторът потвърждава профила.

ЗАЩО СЪЩЕСТВУВА
---------------
``dxf/convention_profile.py`` предлага ролите по СЪДЪРЖАНИЕ, не по име на слой,
и носи за всяко предложение доказателство с думи, увереност и три примерни
handle-а. Без този екран предложението е безполезно: конвенцията става профил
само когато човек я декларира.

Екранът показва и **отказите** — какво програмата не е разбрала и защо. Това е
частта, от която операторът има най-голяма нужда при нов проектант.

Записът минава през ``project/convention_profile_decisions.py`` с автор, време и
самоличност на данните. Мотив се иска само когато операторът се разминава с
измереното.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

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

from lion_rebar.dxf.convention_profile import PROPOSABLE_ROLES, ROLE_NAMES
from lion_rebar.project.convention_profile_decisions import (
    ACCEPTED,
    CHANGED,
    REJECTED,
    ConventionProfileError,
    record_convention_profile,
)
from lion_rebar.project.data_identity import DataIdentity

#: Какво стои в падащото меню. Стойността е (решение, роля-или-None).
НЕ_СЕГА = ("", None)


class DrawingPassportDialog(QDialog):
    """Показва предложения профил и записва операторското потвърждение."""

    def __init__(
        self,
        proposal: dict[str, Any],
        project_root: Path | str,
        identity: DataIdentity,
        operator: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("LION REBAR — Паспорт на чертежа")
        self.resize(1180, 620)
        self._proposal = dict(proposal or {})
        self._rows: list[dict[str, Any]] = [
            dict(ред)
            for ред in self._proposal.get("proposals", [])
            if isinstance(ред, dict)
        ]
        self._refusals: list[dict[str, Any]] = [
            dict(ред)
            for ред in self._proposal.get("refusals", [])
            if isinstance(ред, dict)
        ]
        self._project_root = Path(project_root)
        self._identity = identity
        self._operator = str(operator or "").strip() or "оператор"
        self._verdict_boxes: list[QComboBox] = []
        self._note_edits: list[QLineEdit] = []
        self.decisions_made = 0

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Програмата предлага ролята на всеки слой по СЪДЪРЖАНИЕТО му, не по "
            "името. За всяко предложение виждаш доказателството и можеш да го "
            "приемеш, да смениш ролята или да го отхвърлиш.\n\n"
            "Мотив се иска само когато решиш различно от предложеното. "
            "Потвърденото се записва с твоето име, времето и самоличността на "
            "данните; профил от друг чертеж не се наследява."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addWidget(QLabel(f"<b>Предложения ({len(self._rows)})</b>"))
        self.table = QTableWidget(len(self._rows), 5, self)
        self.table.setHorizontalHeaderLabels(
            ["Слой / блок", "Предложена роля", "Доказателство", "Решение", "Мотив"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for row, предложение in enumerate(self._rows):
            self.table.setItem(row, 0, _само_за_четене(_цел(предложение)))
            роля = str(предложение.get("role") or "")
            увереност = float(предложение.get("confidence") or 0.0)
            self.table.setItem(
                row,
                1,
                _само_за_четене(f"{ROLE_NAMES.get(роля, роля)}  ({увереност:.2f})"),
            )
            доказателство = str(предложение.get("evidence") or "")
            примери = ", ".join(предложение.get("sample_handles") or ())
            if примери:
                доказателство = f"{доказателство}  [примери: {примери}]"
            self.table.setItem(row, 2, _само_за_четене(доказателство))

            combo = QComboBox(self.table)
            combo.addItem("— не решавай сега —", НЕ_СЕГА)
            combo.addItem(
                f"Приемам: {ROLE_NAMES.get(роля, роля)}", (ACCEPTED, роля)
            )
            for друга in PROPOSABLE_ROLES:
                if друга == роля:
                    continue
                combo.addItem(
                    f"Смени на: {ROLE_NAMES.get(друга, друга)}", (CHANGED, друга)
                )
            combo.addItem("Отхвърлям предложението", (REJECTED, ""))
            self.table.setCellWidget(row, 3, combo)
            self._verdict_boxes.append(combo)

            note = QLineEdit(self.table)
            note.setPlaceholderText("Задължителен при смяна или отхвърляне")
            self.table.setCellWidget(row, 4, note)
            self._note_edits.append(note)
        layout.addWidget(self.table)

        layout.addWidget(
            QLabel(f"<b>Какво програмата НЕ разбра ({len(self._refusals)})</b>")
        )
        self.refusal_table = QTableWidget(len(self._refusals), 3, self)
        self.refusal_table.setHorizontalHeaderLabels(
            ["Слой / блок", "Правило", "Защо отказва"]
        )
        self.refusal_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        for row, отказ in enumerate(self._refusals):
            self.refusal_table.setItem(row, 0, _само_за_четене(_цел(отказ)))
            роля = str(отказ.get("role") or "")
            self.refusal_table.setItem(
                row, 1, _само_за_четене(ROLE_NAMES.get(роля, роля))
            )
            self.refusal_table.setItem(
                row, 2, _само_за_четене(str(отказ.get("reason") or ""))
            )
        layout.addWidget(self.refusal_table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Потвърди профила"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Откажи")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- само за тестове и за бутона -------------------------------------- #

    def pending_decisions(self) -> list[dict[str, Any]]:
        """Каквото операторът е избрал, готово за записване."""

        решения: list[dict[str, Any]] = []
        for предложение, combo, note in zip(
            self._rows, self._verdict_boxes, self._note_edits
        ):
            решение, роля = combo.currentData() or НЕ_СЕГА
            if not решение:
                continue
            решения.append(
                {
                    "target_kind": str(предложение.get("target_kind") or "LAYER"),
                    "layer": str(предложение.get("layer") or ""),
                    "block_name": str(предложение.get("block_name") or ""),
                    "role": str(роля or ""),
                    "verdict": решение,
                    "proposed_role": str(предложение.get("role") or ""),
                    "evidence": str(предложение.get("evidence") or ""),
                    "note": note.text().strip(),
                }
            )
        return решения

    def _save(self) -> None:
        решения = self.pending_decisions()
        if not решения:
            QMessageBox.information(
                self,
                "Няма решения",
                "Не е взето решение за нито един слой. Профилът не се записва.",
            )
            return
        try:
            записани = record_convention_profile(
                self._project_root,
                decisions=решения,
                confirmed_by=self._operator,
                identity=self._identity,
                proposer_version=str(self._proposal.get("proposer_version") or ""),
            )
        except ConventionProfileError as error:
            QMessageBox.warning(self, "Профилът не беше записан", str(error))
            return
        self.decisions_made = len(записани)
        QMessageBox.information(
            self,
            "Профилът е потвърден",
            f"Записани са {len(записани)} решения.\n\n"
            "Профилът още НЕ влиза в разпознаването — записан е, за да бъде "
            "прегледан и използван, когато това бъде включено.",
        )
        self.accept()


def _цел(ред: dict[str, Any]) -> str:
    слой = str(ред.get("layer") or "")
    блок = str(ред.get("block_name") or "")
    if блок:
        return f"{слой} / блок {блок}" if слой else f"блок {блок}"
    return слой or "—"


def _само_за_четене(текст: str) -> QTableWidgetItem:
    item = QTableWidgetItem(текст)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item
