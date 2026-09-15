"""Начертаната рамка остава видима върху чертежа (rc44).

Собственик, 2026-09-15: „рамката с два клика трябва да е видима за да може
оператора да вижда какво е маркирал."

КАКВО БЕШЕ КРИВО
----------------
Правоъгълникът живееше само докато се тегли. При втория клик
``mousePressEvent`` вика ``cancel_operator_box`` -> ``_clear_box_preview`` и
рамката изчезва в мига, в който бъде приета. Запазените области не се
рисуваха никъде — виждаха се само като текстов списък в „Начертани рамки…".
Операторът ограждаше на сляпо и нямаше как да провери какво е хванал, преди
да пусне анализа.

ЗАЩО Е ОТДЕЛЕН СПИСЪК ОТ МАРКЕРИТЕ
----------------------------------
Маркерите са временна диагностика и се изчистват при всяко показване
(``show_markers`` вика ``clear_markers``). Рамките са запазено състояние и
трябва да преживеят това чистене — иначе „Покажи гредите и несвързаното"
би изтрило всичко, което операторът е оградил.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
)

from lion_rebar.ui.drawing_view import DrawingView  # noqa: E402

#: Две рамки в координати на ЧЕРТЕЖА (положителен Y, както е в DXF-а).
_РАМКИ = [
    (1000.0, 2000.0, 1800.0, 2600.0, "Гр. B.7 25/55R", "#ef6c00"),
    (2200.0, 2000.0, 3000.0, 2600.0, "К11", "#2e7d32"),
]


def _view() -> DrawingView:
    QApplication.instance() or QApplication([])
    view = DrawingView()
    scene = QGraphicsScene()
    scene.setSceneRect(0.0, -3000.0, 4000.0, 3000.0)
    view.setScene(scene)
    view.resize(800, 600)
    return view


def _правоъгълници(view) -> list[QGraphicsRectItem]:
    """Само самите рамки.

    Подложката под надписа също е правоъгълник, но живее в ЕКРАННИ
    единици (``ItemIgnoresTransformations``), докато рамката е в
    координатите на сцената. Това ги различава еднозначно.
    """

    from PySide6.QtWidgets import QGraphicsItem

    return [
        i
        for i in view._area_items
        if isinstance(i, QGraphicsRectItem)
        and not i.flags() & QGraphicsItem.ItemIgnoresTransformations
    ]


def _надписи(view) -> list[str]:
    return [
        i.text()
        for i in view._area_items
        if isinstance(i, QGraphicsSimpleTextItem)
    ]


class TestВидимост:
    def test_рамката_остава_на_чертежа(self) -> None:
        view = _view()
        assert not view.has_operator_areas()
        view.show_operator_areas(_РАМКИ)
        assert view.has_operator_areas()
        assert len(_правоъгълници(view)) == 2

    def test_заглавието_се_изписва(self) -> None:
        view = _view()
        view.show_operator_areas(_РАМКИ)
        assert _надписи(view) == ["Гр. B.7 25/55R", "К11"]

    def test_правоъгълникът_е_на_мястото_на_рамката(self) -> None:
        """Сцената е с обърнат Y — рамката трябва да ляга там, където е кликнато."""

        view = _view()
        view.show_operator_areas([_РАМКИ[0]])
        rect = _правоъгълници(view)[0].rect()
        assert rect.left() == pytest.approx(1000.0)
        assert rect.right() == pytest.approx(1800.0)
        # y 2000..2600 в чертежа -> -2600..-2000 на сцената
        assert rect.top() == pytest.approx(-2600.0)
        assert rect.bottom() == pytest.approx(-2000.0)


class TestНеПречи:
    def test_рамката_не_поема_кликове(self) -> None:
        """Иначе операторът не може да избере обект под нея."""

        view = _view()
        view.show_operator_areas(_РАМКИ)
        for item in view._area_items:
            assert item.acceptedMouseButtons() == Qt.MouseButtons(Qt.NoButton)

    def test_маркерите_не_трият_рамките(self) -> None:
        """Двата списъка са независими: диагностика срещу запазено състояние."""

        view = _view()
        view.show_operator_areas(_РАМКИ)
        view.show_markers([(1200.0, 2200.0, "поз. 5")], colour="#c62828")
        assert view.has_operator_areas()
        assert len(_правоъгълници(view)) == 2
        view.clear_markers()
        assert view.has_operator_areas()

    def test_рамките_не_трият_маркерите(self) -> None:
        view = _view()
        view.show_markers([(1200.0, 2200.0, "поз. 5")], colour="#c62828")
        view.show_operator_areas(_РАМКИ)
        assert view.has_markers()


class TestОбновяване:
    def test_повторното_показване_не_трупа(self) -> None:
        """След оттегляне на рамка старата не бива да остава на чертежа."""

        view = _view()
        view.show_operator_areas(_РАМКИ)
        view.show_operator_areas([_РАМКИ[0]])
        assert len(_правоъгълници(view)) == 1
        assert _надписи(view) == ["Гр. B.7 25/55R"]

    def test_изчистването_маха_всичко(self) -> None:
        view = _view()
        view.show_operator_areas(_РАМКИ)
        view.clear_operator_areas()
        assert not view.has_operator_areas()
        assert view._area_items == []


class TestКлавишът:
    def test_рамката_е_вързана_за_R(self) -> None:
        """Собственик: „да е с бърз бутон, който да е описан в менюто."

        Проверката е по изходния код, а не по сглобен прозорец: ``MainWindow``
        иска engine registry, дневник и сесия, а тук се пита едно-единствено
        нещо — че действието носи клавиша. Qt сам го изписва в менюто.
        """

        from pathlib import Path

        import lion_rebar.ui.main_window as mw

        код = Path(mw.__file__).read_text(encoding="utf-8")
        начало = код.index("self.action_operator_box = QAction")
        край = код.index("self.action_show_unresolved")
        парче = код[начало:край]
        assert 'setShortcut(QKeySequence("R"))' in парче

    def test_R_не_е_зает_другаде_в_главния_прозорец(self) -> None:
        """``F`` вече е „Покажи целия чертеж“ — ``R`` трябва да е само за рамката."""

        from pathlib import Path
        import re

        import lion_rebar.ui.main_window as mw

        код = Path(mw.__file__).read_text(encoding="utf-8")
        единични = re.findall(r'setShortcut\(QKeySequence\("([A-Z])"\)\)', код)
        единични += re.findall(r'setShortcut\("([A-Z])"\)', код)
        assert единични.count("R") == 1, f"клавишът R е зает повече от веднъж: {единични}"
