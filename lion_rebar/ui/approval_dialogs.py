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
    """Диалогът за одобрение: кой, защо и контролната фраза.

    ``default_by`` и ``default_note`` предлагат стойности, които програмата
    вече знае — последния одобрил от регистъра и какво точно добавя черновата.

    Собственик, 22.09.2026: „трябва ли да пиша всеки път тези текстове“. Не —
    предложеното е редактируемо и не се записва, докато не натисне OK.

    КОНТРОЛНАТА ФРАЗА НИКОГА НЕ СЕ ПРЕДЛАГА. Тя е единственото нещо тук, което
    не може да дойде от програмата: тя е разликата между „записах чернова“ и
    „смених производствения engine“.
    """

    def __init__(
        self,
        title: str,
        required_phrase: str,
        note_label: str = "Причина / бележка:",
        parent=None,
        *,
        default_by: str = "",
        default_note: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.required_phrase = required_phrase
        self.by_edit = QLineEdit()
        self.by_edit.setText(str(default_by or ""))
        self.note_edit = QPlainTextEdit()
        self.note_edit.setMaximumHeight(100)
        self.note_edit.setPlainText(str(default_note or ""))
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
