# -*- coding: utf-8 -*-
"""Лентата на напредъка при експорт не пипа зануления диалог.

## Дефектът

При всеки Excel + DXF експорт в дневника излизаше:

    File "lion_rebar/ui/main_window.py", in _export_progress_updated
        self._export_progress.setLabelText(…)
    AttributeError: 'NoneType' object has no attribute 'setLabelText'

Проверката за ``None`` Я ИМАШЕ един ред по-горе. Причината е повторна
влизаемост, класическа за Qt: ``QProgressDialog.setValue()`` върти цикъла на
събитията, когато диалогът е модален. Докато го върти, пристига сигналът за
край, ``_export_finished`` занулява ``self._export_progress``, и следващият ред
на ``_export_progress_updated`` пипа ``None``.

## Как се проверява без прозорец

Методите се викат върху обикновен обект чрез ``__get__``: така се изпълнява
СЪЩИЯТ код на ``MainWindow``, без да се вдига Qt. Диалогът е заместен с обект,
чийто ``setValue`` прави точно това, което прави истинският — влиза повторно и
занулява полето.

Доказано червено срещу стария код: със старата подредба
(``setValue`` преди ``setLabelText``, без местна препратка)
``test_setValue_koeto_zanulyava_dialoga_ne_chupi`` дава същия ``AttributeError``.
"""
from __future__ import annotations

import pytest

from lion_rebar.ui.main_window import MainWindow


class ЛентаКоятоЗанулява:
    """Диалог, чийто ``setValue`` влиза повторно — както прави Qt."""

    def __init__(self, собственик):
        self.собственик = собственик
        self.стойности: list[int] = []
        self.надписи: list[str] = []
        self.затворен = False
        self.изтрит = False

    def setValue(self, стойност):
        self.стойности.append(int(стойност))
        # Точно каквото прави QProgressDialog: върти цикъла, а през него
        # ``_export_finished`` занулява полето на собственика.
        self.собственик._export_progress = None

    def setLabelText(self, текст):
        self.надписи.append(str(текст))

    def close(self):
        self.затворен = True

    def deleteLater(self):
        self.изтрит = True


class ЛентаНаСъстоянието:
    def __init__(self):
        self.съобщения: list[str] = []

    def showMessage(self, текст, _timeout=0):
        self.съобщения.append(str(текст))


class Прозорец:
    """Минимумът, който двата метода пипат."""

    def __init__(self):
        self._export_progress = None
        self._лента = ЛентаНаСъстоянието()

    def statusBar(self):
        return self._лента


@pytest.fixture()
def прозорец():
    п = Прозорец()
    п._export_progress = ЛентаКоятоЗанулява(п)
    return п


def напредък(прозорец, percent, label="Excel"):
    MainWindow._export_progress_updated.__get__(прозорец)(percent, label)


# --------------------------------------------------------------------------
# Самият дефект
# --------------------------------------------------------------------------

def test_setValue_koeto_zanulyava_dialoga_ne_chupi(прозорец):
    """СЪРЦЕВИНАТА: ``setValue`` занулява полето, методът пак приключва."""

    диалог = прозорец._export_progress
    напредък(прозорец, 40, "Excel листове")
    assert прозорец._export_progress is None        # занулено отвътре
    assert диалог.стойности == [40]
    assert диалог.надписи == ["Excel листове\nПрогрес: 40%"]


def test_nadpisat_se_pishe_predi_stoynostta(прозорец):
    """Редът е важен: надписът ПРЕДИ стойността, защото тя върти цикъла."""

    диалог = прозорец._export_progress
    напредък(прозорец, 10)
    # Ако стойността беше първа, надписът нямаше да се напише изобщо.
    assert диалог.надписи and диалог.стойности


def test_bez_dialog_ne_gurmi():
    п = Прозорец()
    напредък(п, 55, "DXF")
    assert п._лента.съобщения == ["Excel + DXF export: 55% — DXF"]


@pytest.mark.parametrize("вход,очакван", [
    (-10, 0), (0, 0), (55, 55), (100, 100), (250, 100), (7.9, 7),
])
def test_stoynostta_se_ogranichava_v_0_100(вход, очакван):
    п = Прозорец()
    п._export_progress = ЛентаКоятоЗанулява(п)
    напредък(п, вход)
    assert п._export_progress is None
    assert п._лента.съобщения[-1].endswith("%d%% — Excel" % очакван)


def test_lentata_na_sastoyanieto_se_pishe_vinagi(прозорец):
    """Дори при занулен диалог състоянието стига до оператора."""

    напредък(прозорец, 80, "DXF маркери")
    assert прозорец._лента.съобщения == [
        "Excel + DXF export: 80% — DXF маркери"]
