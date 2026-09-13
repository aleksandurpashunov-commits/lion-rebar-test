"""Рамката излъчва координати на ЧЕРТЕЖА, не на екрана (rc44).

Изгледът е top-view с обърнат Y: всеки обект се поставя на ``-y``, за да сочи
екранното „надолу" натам, накъдето сочи и на хартия. ``mouseMoveEvent`` вече
обръщаше Y обратно, преди да покаже координатите на показалеца;
``mousePressEvent`` не го правеше и излъчваше суровата сценична стойност.

Затова рамката с два клика не можеше да работи изобщо: в ``s-9`` заглавията
на гредите стоят на y≈+2678, а рамката ги търсеше около y≈-2678 и връщаше
„В рамката няма заглавие" при всяко чертане. Модулните тестове на
``operator_group_box`` бяха зелени през цялото време — те получават готови
координати и никога не минават през изгледа.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsScene  # noqa: E402

from lion_rebar.ui.drawing_view import DrawingView  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _click(view: DrawingView, scene_x: float, scene_y: float) -> None:
    """Кликва там, където дадената СЦЕНИЧНА точка се вижда на екрана."""

    viewport_point = view.mapFromScene(QPointF(scene_x, scene_y))
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(viewport_point),
        view.viewport().mapToGlobal(QPoint(viewport_point.x(), viewport_point.y())),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.mousePressEvent(event)


def _move(view: DrawingView, scene_x: float, scene_y: float) -> None:
    """Мести показалеца до дадената СЦЕНИЧНА точка."""

    viewport_point = view.mapFromScene(QPointF(scene_x, scene_y))
    event = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(viewport_point),
        view.viewport().mapToGlobal(QPoint(viewport_point.x(), viewport_point.y())),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.mouseMoveEvent(event)

def test_двата_клика_дават_чертожни_координати() -> None:
    """Клик върху обект, нарисуван на сцената при ``-y``, връща ``+y``.

    Заглавието „Гр. B.19 25/55" е на y=+2678 в чертежа, тоест на y=-2678 на
    сцената. Рамката около него трябва да излезе с чертожните стойности,
    иначе никое заглавие никога не попада вътре.
    """

    _app()
    view = DrawingView()
    scene = QGraphicsScene()
    scene.setSceneRect(113000.0, -3000.0, 2000.0, 2000.0)
    view.setScene(scene)
    view.resize(800, 600)
    view.fitInView(scene.sceneRect())

    получени: list[tuple[float, float, float, float]] = []
    view.operatorBoxDrawn.connect(
        lambda x0, y0, x1, y1: получени.append((x0, y0, x1, y1))
    )

    view.begin_operator_box()
    _click(view, 113300.0, -2600.0)
    _click(view, 113900.0, -2950.0)

    assert len(получени) == 1
    x0, y0, x1, y1 = получени[0]
    assert y0 == pytest.approx(2600.0, abs=20.0)
    assert y1 == pytest.approx(2950.0, abs=20.0)
    assert x0 == pytest.approx(113300.0, abs=20.0)
    assert x1 == pytest.approx(113900.0, abs=20.0)


def test_режимът_се_самоизключва_след_втория_клик() -> None:
    _app()
    view = DrawingView()
    scene = QGraphicsScene()
    scene.setSceneRect(0.0, -1000.0, 1000.0, 1000.0)
    view.setScene(scene)
    view.resize(400, 400)
    view.fitInView(scene.sceneRect())

    брой: list[int] = []
    view.operatorBoxDrawn.connect(lambda *_: брой.append(1))

    view.begin_operator_box()
    _click(view, 100.0, -100.0)
    _click(view, 500.0, -500.0)
    # Трети и четвърти клик: режимът вече е изключен, нищо не се излъчва.
    _click(view, 600.0, -600.0)
    _click(view, 700.0, -700.0)

    assert len(брой) == 1


def test_рамката_се_вижда_докато_се_чертае() -> None:
    """След първия клик пунктиран правоъгълник следва показалеца.

    Без него операторът чертае на сляпо и разбира какво е обхванал чак
    когато рамката бъде приета или отказана.
    """

    _app()
    view = DrawingView()
    scene = QGraphicsScene()
    scene.setSceneRect(0.0, -1000.0, 1000.0, 1000.0)
    view.setScene(scene)
    view.resize(400, 400)
    view.fitInView(scene.sceneRect())

    view.begin_operator_box()
    assert view._box_preview is None

    _click(view, 100.0, -100.0)
    _move(view, 500.0, -600.0)

    assert view._box_preview is not None
    rect = view._box_preview.rect()
    assert rect.width() == pytest.approx(400.0, abs=20.0)
    assert rect.height() == pytest.approx(500.0, abs=20.0)

    # Вторият клик завършва рамката и прегледът се маха.
    _click(view, 500.0, -600.0)
    assert view._box_preview is None


def test_отказът_маха_прегледа() -> None:
    _app()
    view = DrawingView()
    scene = QGraphicsScene()
    scene.setSceneRect(0.0, -1000.0, 1000.0, 1000.0)
    view.setScene(scene)
    view.resize(400, 400)
    view.fitInView(scene.sceneRect())

    view.begin_operator_box()
    _click(view, 100.0, -100.0)
    _move(view, 500.0, -600.0)
    assert view._box_preview is not None

    view.cancel_operator_box()

    assert view._box_preview is None
    assert not view._box_mode
