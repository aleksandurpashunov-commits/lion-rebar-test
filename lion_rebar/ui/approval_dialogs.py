from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)


@dataclass(frozen=True)
class ApprovalData:
    by: str
    note: str
    phrase: str


class ApprovalDialog(QDialog):
    def __init__(self, title: str, required_phrase: str, note_label: str = "Причина / бележка:", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.required_phrase = required_phrase
        self.by_edit = QLineEdit()
        self.note_edit = QPlainTextEdit()
        self.note_edit.setMaximumHeight(100)
        self.phrase_edit = QLineEdit()

        instruction = QLabel(
            f"Въведи точно следната фраза:\n<b>{required_phrase}</b>"
        )
        instruction.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Одобрил / автор:", self.by_edit)
        form.addRow(note_label, self.note_edit)
        form.addRow("Контролна фраза:", self.phrase_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(instruction)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def data(self) -> ApprovalData:
        return ApprovalData(
            by=self.by_edit.text().strip(),
            note=self.note_edit.toPlainText().strip(),
            phrase=self.phrase_edit.text(),
        )
