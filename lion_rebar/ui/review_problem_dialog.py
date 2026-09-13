from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from lion_rebar.review_center_model import build_review_evidence_note


PROBLEM_REASONS: tuple[tuple[str, str], ...] = (
    ("NO_VALID_CANDIDATE", "Няма верен кандидат"),
    ("WRONG_OBJECT", "Маркира чужда позиция / друг диаметър"),
    ("PARTIAL_GEOMETRY", "Маркира само част от желязото"),
    ("WRONG_MASTER", "Геометрията е вярна, но MASTER формата е грешна"),
    ("DXF_DIMENSION_CONFLICT", "DXF геометрията противоречи на надписа/размерите"),
    ("PROJECTANT_ERROR", "Проектантска грешка в надписа, размера или геометрията"),
    ("DUPLICATE_GEOMETRY", "Същата геометрия е предложена за друг надпис"),
    ("MISSING_MASTER", "Липсва правилна MASTER форма"),
    ("NO_GEOMETRY", "Няма видима геометрия в DXF"),
    ("OTHER", "Друг проблем"),
)


class ReviewProblemDialog(QDialog):
    """Record a diagnostic problem without pretending that it is resolved."""

    def __init__(self, *, row: dict, default_reason: str | None = None, parent=None):
        super().__init__(parent)
        self.row = dict(row)
        self.result_payload: dict | None = None
        self.setWindowTitle("Запиши проблем / инженерна бележка")
        self.resize(680, 470)

        intro = QLabel(
            "Записването на проблем НЕ решава позицията и НЕ я изключва автоматично. "
            "След това използвай „Инженерно решение“, за да зададеш точната форма, брой, Ø, L и сегменти. "
            "Изключване от спецификацията е отделно изрично действие."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("background:#fff7ed; border:1px solid #fed7aa; padding:8px;")

        form = QFormLayout()
        form.addRow("Надпис:", QLabel(str(row.get("annotation_text") or "—")))

        self.reason_combo = QComboBox()
        for code, label in PROBLEM_REASONS:
            self.reason_combo.addItem(label, code)
        if default_reason:
            index = self.reason_combo.findData(default_reason)
            if index >= 0:
                self.reason_combo.setCurrentIndex(index)
        form.addRow("Какво не е наред:", self.reason_combo)

        self.correct_edit = QPlainTextEdit()
        self.correct_edit.setPlaceholderText(
            "Напиши как трябва да бъде — например: F005, 60N8x94, 40+14+40; или „няма геометрия в чертежа“."
        )
        self.correct_edit.setMaximumHeight(95)
        form.addRow("Как трябва да бъде:", self.correct_edit)

        self.note_edit = QPlainTextEdit()
        self._auto_note = build_review_evidence_note(
            row,
            reason_label=self.reason_combo.currentText(),
        )
        self.note_edit.setPlainText(self._auto_note)
        self.note_edit.setPlaceholderText("Техническа бележка / проектантска особеност.")
        self.note_edit.setMaximumHeight(110)
        form.addRow("Бележка и доказателство:", self.note_edit)

        self.reject_candidates_check = QCheckBox(
            "Запомни показаните кандидати като отхвърлени за този надпис"
        )
        self.reject_candidates_check.setChecked(False)

        self.exclude_check = QCheckBox(
            "ИЗРИЧНО ИЗКЛЮЧИ позицията от производствената спецификация"
        )
        self.exclude_check.setChecked(False)
        self.exclude_check.setStyleSheet("font-weight:700; color:#991b1b;")
        self.exclude_check.setToolTip(
            "Изключената позиция няма да влезе в Excel. Пълният production export остава блокиран, "
            "докато има изключени позиции."
        )
        self.reason_combo.currentIndexChanged.connect(self._reason_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Запиши проблема")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.reject_candidates_check)
        layout.addWidget(self.exclude_check)
        layout.addWidget(buttons)

    def _reason_changed(self, _index: int = -1) -> None:
        current = self.note_edit.toPlainText().strip()
        if current and current != self._auto_note:
            return
        self._auto_note = build_review_evidence_note(
            self.row,
            reason_label=self.reason_combo.currentText(),
        )
        self.note_edit.setPlainText(self._auto_note)

    def _accept(self) -> None:
        self.result_payload = {
            "reason_code": str(self.reason_combo.currentData() or "OTHER"),
            "reason": self.reason_combo.currentText(),
            "correct_specification": self.correct_edit.toPlainText().strip(),
            "note": self.note_edit.toPlainText().strip(),
            "reject_candidates": self.reject_candidates_check.isChecked(),
            "explicit_exclusion": self.exclude_check.isChecked(),
        }
        self.accept()
