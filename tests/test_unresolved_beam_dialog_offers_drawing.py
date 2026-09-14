"""Списъкът с греди трябва да има изход към чертежа (rc44).

Собственик, 2026-09-13, като видя диалога с осемте позиции: „и как да ги
посоча?"

Въпросът е основателен и е същият, който той зададе и по-рано: списъкът
предлага 26 падащи менюта и нито един ориентир КЪДЕ на чертежа е всяка
позиция. Изборът по памет между 26 греди не е работа за човек — той вече
провери подредените по близост предложения и отсече „никое не е вярно".

Разумният ход е обратният: да се види чертежът и да се огради гредата заедно
с позициите ѝ. Затова диалогът има трети бутон, който го затваря и подкарва
точно това. Изборът от списък остава за единични случаи, когато операторът
вече знае отговора.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QDialogButtonBox,
)

from lion_rebar.project.data_identity import DataIdentity  # noqa: E402
from lion_rebar.ui.unresolved_beam_dialog import UnresolvedBeamDialog  # noqa: E402

_POSITIONS = [
    ("14C11::BJ-FI-L", "3N16x685"),
    ("14C1F::BJ-FI-L", "2N8x685"),
]
_BEAMS = [
    ("BL19", "Гр. B.19 25/55"),
    ("BL20", "Гр. B.20 25/40"),
    ("BL30", "Гр. B.30 25/40"),
]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _dialog(tmp_path: Path) -> UnresolvedBeamDialog:
    _app()
    return UnresolvedBeamDialog(
        _POSITIONS,
        _BEAMS,
        tmp_path,
        DataIdentity(
            project_id="s-9", source_sha256="SHA",
            source_generation_id="G1", analysis_run_id="R1",
        ),
        "Александър",
    )


def _buttons(dialog: UnresolvedBeamDialog) -> QDialogButtonBox:
    box = dialog.findChild(QDialogButtonBox)
    assert box is not None
    return box


def test_има_изход_към_чертежа(tmp_path: Path) -> None:
    """Трите избора: запиши, откажи, или иди и ги огради."""

    dialog = _dialog(tmp_path)

    texts = [button.text() for button in _buttons(dialog).buttons()]

    assert any("чертежа" in text for text in texts), texts
    assert any("Запиши" in text for text in texts), texts
    assert any("Откажи" in text for text in texts), texts


def test_бутонът_вдига_флага_и_затваря(tmp_path: Path) -> None:
    """Главният прозорец разпознава флага и показва маркерите."""

    dialog = _dialog(tmp_path)
    assert not dialog.wants_drawing_overview

    button = next(
        b for b in _buttons(dialog).buttons() if "чертежа" in b.text()
    )
    button.click()

    assert dialog.wants_drawing_overview
    assert not dialog.isVisible()


def test_отказът_не_вдига_флага(tmp_path: Path) -> None:
    """„Откажи" си остава отказ, не заобиколен път към чертежа."""

    dialog = _dialog(tmp_path)

    dialog.reject()

    assert not dialog.wants_drawing_overview


def test_въведението_казва_какво_да_прави_операторът(tmp_path: Path) -> None:
    """Текстът трябва да признава, че списъкът сам по себе си не стига."""

    dialog = _dialog(tmp_path)

    texts = " ".join(
        label.text() for label in dialog.findChildren(type(dialog.findChild(type(dialog))))
        if hasattr(label, "text")
    )
    intro = " ".join(
        child.text()
        for child in dialog.children()
        if hasattr(child, "text") and isinstance(getattr(child, "text")(), str)
    )
    combined = texts + intro

    assert "чертежа" in combined or "огражда" in combined


def test_изборът_от_списък_остава_възможен(tmp_path: Path) -> None:
    """Новият бутон не отнема стария път — той е за единични случаи."""

    dialog = _dialog(tmp_path)

    combos = dialog.findChildren(QComboBox)

    assert len(combos) == len(_POSITIONS)
    # „не решавай сега" плюс по едно на греда.
    assert combos[0].count() == len(_BEAMS) + 1
    assert combos[0].currentData() == ""


class TestЛипсващаОбосновка:
    """Собственик, 2026-09-14: „не ми дава да ги запазя."

    Причината беше двойна. Проверката спираше на ПЪРВАТА липсваща
    обосновка, тъй че при няколко празни реда предупреждението излизаше
    отново и отново, все едно и също. И по-важно: когато осем позиции
    отиват на една греда по една и съща причина, осемкратното изписване на
    същия мотив не е решение, а преписване.

    Мотивът остава задължителен — той прави решението проследимо. Просто
    вече може да се въведе веднъж.
    """

    def _prepared(self, tmp_path: Path, с_обосновка: list[int]):
        dialog = _dialog(tmp_path)
        for combo in dialog._beam_boxes:
            combo.setCurrentIndex(1)
        for index in с_обосновка:
            dialog._note_edits[index].setText("вярно")
        return dialog

    def test_намира_всички_липсващи_наведнъж(self, tmp_path: Path) -> None:
        dialog = self._prepared(tmp_path, [0])

        assert dialog._missing_notes() == [1]

    def test_без_избрана_греда_не_иска_обосновка(self, tmp_path: Path) -> None:
        """Позиция без избор остава нерешена — не е грешка."""

        dialog = _dialog(tmp_path)

        assert dialog._missing_notes() == []

    def test_попълнените_не_се_броят(self, tmp_path: Path) -> None:
        dialog = self._prepared(tmp_path, [0, 1])

        assert dialog._missing_notes() == []

    def test_общият_мотив_попълва_само_празните(self, tmp_path: Path) -> None:
        """Написаното от оператора не се презаписва."""

        from PySide6.QtWidgets import QInputDialog, QMessageBox

        dialog = self._prepared(tmp_path, [0])
        dialog._note_edits[0].setText("собствена бележка")

        натиснат = {}

        def _exec(self):
            цел = [b for b in self.buttons() if "всички" in b.text()]
            натиснат["има_бутон"] = bool(цел)
            цел[0].click()
            return 0

        оригинал_exec = QMessageBox.exec
        оригинал_text = QInputDialog.getText
        QMessageBox.exec = _exec
        QInputDialog.getText = staticmethod(lambda *a, **k: ("общ мотив", True))
        try:
            резултат = dialog._offer_shared_note(dialog._missing_notes())
        finally:
            QMessageBox.exec = оригинал_exec
            QInputDialog.getText = оригинал_text

        assert натиснат["има_бутон"]
        assert резултат is True
        assert dialog._note_edits[0].text() == "собствена бележка"
        assert dialog._note_edits[1].text() == "общ мотив"

    def test_отказан_общ_мотив_не_записва(self, tmp_path: Path) -> None:
        """Празна обосновка не минава по никой път."""

        from PySide6.QtWidgets import QInputDialog, QMessageBox

        dialog = self._prepared(tmp_path, [0])

        оригинал_exec = QMessageBox.exec
        оригинал_text = QInputDialog.getText
        QMessageBox.exec = lambda self: (
            [b for b in self.buttons() if "всички" in b.text()][0].click() or 0
        )
        QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))
        try:
            резултат = dialog._offer_shared_note(dialog._missing_notes())
        finally:
            QMessageBox.exec = оригинал_exec
            QInputDialog.getText = оригинал_text

        assert резултат is False
        assert dialog._note_edits[1].text() == ""
