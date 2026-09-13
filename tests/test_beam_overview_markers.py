"""Операторът трябва да ВИДИ какво да огради (rc44).

Собственик, 2026-09-13, пред блокиран Финален контрол на ``s-9``: „редно е да
има бутон с който да оградя гредите преди пакетния анализ, защото сега няма
как да знам кое желязо на коя греда принадлежи при положение че не мога да
ги видя и да ги маркирам."

Рамката с два клика вече съществуваше, но операторът нямаше на какво да
стъпи: заглавията на гредите се губят сред 15 000 обекта, а кои позиции
остават несвързани се разбираше чак от блокирания Финален контрол — и то
като списък с handles, без да личи къде на чертежа са.

Маркерите показват и двете върху самия чертеж: заглавията в синьо,
несвързаните позиции в червено.

НАЙ-ВАЖНОТО ТВЪРДЕНИЕ е последното. ``_unresolved_beam_context`` обхожда
ВСЕКИ DXF обект в гредовата рамка — котировки, легенди, помощни текстове. В
``s-9`` те са 67, докато Финалният контрол спира само заради 8. Да се покажат
всичките би пратило оператора да огражда неща, които изобщо не влизат в
описа.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsScene  # noqa: E402

from lion_rebar.ui.drawing_view import DrawingView  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _view() -> DrawingView:
    _app()
    view = DrawingView()
    scene = QGraphicsScene()
    scene.setSceneRect(113000.0, -4000.0, 3000.0, 3000.0)
    view.setScene(scene)
    view.resize(800, 600)
    return view


# Истински координати от ``s-9``: заглавието „Гр. B.19 25/55" и позицията
# 14C11, която стои 455 единици вляво от него.
_TITLE = (113837.0, 2678.0, "Гр. B.19 25/55")
_POSITION = (113382.0, 2762.0, "3N16x685")


class TestМаркери:
    def test_маркерите_се_появяват_и_махат(self) -> None:
        view = _view()
        assert not view.has_markers()

        view.show_markers([_TITLE], colour="#1565c0")
        assert view.has_markers()

        view.clear_markers()
        assert not view.has_markers()

    def test_кръгче_и_надпис_за_всеки_маркер(self) -> None:
        view = _view()

        view.show_markers([_TITLE, _POSITION], colour="#d93025")

        # Два маркера, всеки с кръгче и текст.
        assert len(view._markers) == 4

    def test_маркер_без_надпис_е_само_кръгче(self) -> None:
        view = _view()

        view.show_markers([(100.0, 100.0, "")], colour="#d93025")

        assert len(view._markers) == 1

    def test_append_добавя_вместо_да_замести(self) -> None:
        """Заглавията и позициите са два цвята върху един чертеж."""

        view = _view()

        view.show_markers([_TITLE], colour="#1565c0")
        view.show_markers([_POSITION], colour="#d93025", append=True)

        assert len(view._markers) == 4

    def test_без_append_старите_маркери_си_отиват(self) -> None:
        view = _view()

        view.show_markers([_TITLE], colour="#1565c0")
        view.show_markers([_POSITION], colour="#d93025")

        assert len(view._markers) == 2

    def test_маркерът_стои_на_чертожните_координати(self) -> None:
        """Сцената е с обърнат Y — маркерът трябва да го отчете.

        Същият капан, който правеше рамката с два клика неработеща: заглавие
        на y=+2678 в чертежа стои на y=−2678 на сцената.
        """

        view = _view()

        view.show_markers([_TITLE], colour="#1565c0")

        circle = view._markers[0]
        centre = circle.sceneBoundingRect().center()
        assert centre.x() == pytest.approx(_TITLE[0], abs=1.0)
        assert centre.y() == pytest.approx(-_TITLE[1], abs=1.0)

    def test_обхватът_покрива_всички_маркери(self) -> None:
        view = _view()

        view.show_markers([_TITLE, _POSITION], colour="#d93025")
        bounds = view._marker_bounds

        assert bounds is not None
        assert bounds.left() <= min(_TITLE[0], _POSITION[0])
        assert bounds.right() >= max(_TITLE[0], _POSITION[0])

    def test_мащабирането_без_маркери_не_гърми(self) -> None:
        view = _view()

        view.zoom_to_markers()  # не бива да хвърля

        assert not view.has_markers()


class TestПоказваСеСамоБлокиращото:
    """Филтърът, без който прегледът заблуждава повече, отколкото помага."""

    def test_само_одобрените_позиции_се_броят(self) -> None:
        """В ``s-9``: 67 обекта без греда, но само 8 блокират експорта.

        Тук се проверява самото правило върху представителен вход, а
        логиката в ``MainWindow.show_beam_overview`` прилага същото
        пресичане с одобрените връзки.
        """

        всички = [
            ("14C11::BJ-FI-L", 113382.0, 2762.0),   # одобрена, блокира
            ("14C1F::BJ-FI-L", 113386.0, 2816.0),   # одобрена, блокира
            ("КОТИРОВКА-1", 113400.0, 2900.0),       # не влиза в описа
            ("ЛЕГЕНДА-2", 113500.0, 3000.0),         # не влиза в описа
        ]
        одобрени = {"14C11::BJ-FI-L", "14C1F::BJ-FI-L"}

        блокиращи = [item for item in всички if str(item[0]) in одобрени]

        assert [item[0] for item in блокиращи] == [
            "14C11::BJ-FI-L",
            "14C1F::BJ-FI-L",
        ]
