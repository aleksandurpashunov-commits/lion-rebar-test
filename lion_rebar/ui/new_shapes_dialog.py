"""Всички нови форми от един чертеж — на един екран.

ЗАЩО. Собственик, 22.09.2026: „адски тромаво и бавно е това, което направихме“.
За ЕДНА форма се минаваше: бутон -> чернова -> одобри (два въпроса) ->
публикувай (три полета и контролна фраза) -> внимавай за номера на версията. А
в един чертеж семействата без MASTER форма са няколко.

Тук се показват всички наведнъж. За всяко семейство: образецът, ъглите,
уравнението, случаите зад него и СИЛУЕТЪТ, начертан с истинските размери от
чертежа. Операторът отмята кои иска, кръщава ги и поправя посоката, ако
силуетът е обърнат.

Какво НЕ се мени: формата пак се ражда като чернова, пак минава през контролни
тестове и одобрение, и пак се публикува с контролна фраза. Смалява се броят
въпроси, не пътят.
"""
from __future__ import annotations

from typing import Any, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lion_rebar.engine.shape_from_case import ShapeProposal, build_draft_shape
from lion_rebar.ui.shape_preview import ShapePreview


class NewShapesDialog(QDialog):
    """Избор, кръщаване и преглед на предложените форми."""

    def __init__(self, proposals: Sequence[tuple[str, ShapeProposal]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Нови форми от този чертеж")
        self.setMinimumSize(980, 620)
        self._proposals = list(proposals)
        self._rows: list[dict[str, Any]] = []

        self.list = QListWidget(self)
        self.list.currentRowChanged.connect(self._show_current)

        подробности = QWidget(self)
        колона = QVBoxLayout(подробности)
        self.title_label = QLabel("", подробности)
        self.title_label.setStyleSheet("font-weight:700; font-size:14px;")
        self.equation_label = QLabel("", подробности)
        self.equation_label.setWordWrap(True)
        self.cases_label = QLabel("", подробности)
        self.cases_label.setWordWrap(True)
        self.cases_label.setStyleSheet("color:#374151;")

        име_ред = QHBoxLayout()
        име_ред.addWidget(QLabel("Име:"))
        self.name_edit = QLineEdit(подробности)
        self.name_edit.textChanged.connect(self._name_changed)
        име_ред.addWidget(self.name_edit, 1)
        self.flip_button = QPushButton("Обърни посоката", подробности)
        self.flip_button.setToolTip(
            "Каноничната сигнатура не пази накъде прескача прътът. Ако силуетът "
            "е обърнат спрямо чертежа, това го оправя."
        )
        self.flip_button.clicked.connect(self._flip_current)
        име_ред.addWidget(self.flip_button)

        self.preview = ShapePreview(подробности)
        self.preview.setMinimumHeight(240)

        колона.addWidget(self.title_label)
        колона.addWidget(self.equation_label)
        колона.addLayout(име_ред)
        колона.addWidget(self.cases_label)
        колона.addWidget(self.preview, 1)

        разделител = QSplitter(Qt.Orientation.Horizontal, self)
        разделител.addWidget(self.list)
        разделител.addWidget(подробности)
        разделител.setStretchFactor(1, 1)

        бележка = QLabel(
            "Отметнатите се раждат като ЧЕРНОВА с контролен тест за всеки случай. "
            "Одобрението и публикуването остават отделни стъпки.",
            self,
        )
        бележка.setWordWrap(True)
        бележка.setStyleSheet("color:#6b7280;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Направи отметнатите")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(разделител, 1)
        layout.addWidget(бележка)
        layout.addWidget(buttons)

        self._fill()
        if self._rows:
            self.list.setCurrentRow(0)

    # ------------------------------------------------------------------ данни

    def _fill(self) -> None:
        for индекс, (код, предложение) in enumerate(self._proposals, start=1):
            име = self._suggest_name(предложение, индекс)
            self._rows.append({
                "code": код,
                "proposal": предложение,
                "name": име,
                "flipped": False,
            })
            item = QListWidgetItem(self._label(self._rows[-1]))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)

    @staticmethod
    def _suggest_name(предложение: ShapeProposal, индекс: int) -> str:
        """Име по устройството на формата, за да не се почва от празно."""

        n = предложение.segment_count
        ъгли = {round(abs(float(a))) for a in предложение.turn_angles_deg}
        if предложение.symmetric and n == 5 and ъгли == {45}:
            return "КОБИЛИЦА"
        if предложение.symmetric:
            return f"НОВА СИМЕТРИЧНА ФОРМА {индекс}"
        return f"НОВА ФОРМА {индекс}"

    @staticmethod
    def _label(ред: dict[str, Any]) -> str:
        п: ShapeProposal = ред["proposal"]
        return "%s — %s (%d участъка, %d случая)" % (
            ред["code"], ред["name"], п.segment_count, п.case_count,
        )

    # ------------------------------------------------------------------ екран

    def _current(self) -> dict[str, Any] | None:
        индекс = self.list.currentRow()
        if 0 <= индекс < len(self._rows):
            return self._rows[индекс]
        return None

    def _show_current(self) -> None:
        ред = self._current()
        if ред is None:
            return
        п: ShapeProposal = ред["proposal"]
        self.title_label.setText("%s — образец %s" % (ред["code"], п.pattern))
        ъгли = ", ".join("%+g°" % a for a in self._angles(ред))
        self.equation_label.setText("%s\nЪгли: %s" % (п.equation, ъгли or "няма"))
        случаи = "; ".join(
            "%s (%s)" % (
                v.case.annotation_text,
                " + ".join("%g" % s for s in v.segment_values_cm),
            )
            for v in п.values
        )
        self.cases_label.setText("Случаи в чертежа: " + случаи)
        self.name_edit.blockSignals(True)
        self.name_edit.setText(ред["name"])
        self.name_edit.blockSignals(False)
        форма = build_draft_shape(self._flipped_proposal(ред),
                                  code=ред["code"], name=ред["name"] or "НОВА")
        self.preview.set_shape(форма)
        първи = п.values[0]
        self.preview.set_production_values(
            първи.segment_values_cm,
            total_length_cm=първи.case.declared_length_cm,
            status_text="по случая %s" % първи.case.annotation_text,
        )

    def _angles(self, ред: dict[str, Any]) -> tuple[float, ...]:
        п: ShapeProposal = ред["proposal"]
        if ред["flipped"]:
            return tuple(-float(a) for a in п.turn_angles_deg)
        return tuple(float(a) for a in п.turn_angles_deg)

    def _flipped_proposal(self, ред: dict[str, Any]) -> ShapeProposal:
        from dataclasses import replace

        п: ShapeProposal = ред["proposal"]
        if not ред["flipped"]:
            return п
        return replace(п, turn_angles_deg=self._angles(ред))

    def _name_changed(self, текст: str) -> None:
        ред = self._current()
        if ред is None:
            return
        ред["name"] = текст
        item = self.list.item(self.list.currentRow())
        if item is not None:
            item.setText(self._label(ред))

    def _flip_current(self) -> None:
        ред = self._current()
        if ред is None:
            return
        ред["flipped"] = not ред["flipped"]
        self._show_current()

    # --------------------------------------------------------------- резултат

    def selected(self) -> list[tuple[str, str, ShapeProposal]]:
        """Отметнатите като ``(код, име, предложение)``."""

        изход: list[tuple[str, str, ShapeProposal]] = []
        for индекс, ред in enumerate(self._rows):
            item = self.list.item(индекс)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            име = " ".join(str(ред["name"]).split())
            if not име:
                continue
            изход.append((ред["code"], име, self._flipped_proposal(ред)))
        return изход
