"""Екранът „Форми от този чертеж…“ — избор, име и обръщане на посоката.

Собственик, 22.09.2026: „адски тромаво и бавно“ (церемонията беше на форма) и
„искам… когато цикна да видя формата дали е вярна, и някакъв начин за корекция
лесен“.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lion_rebar.engine.shape_from_case import (  # noqa: E402
    build_draft_shape,
    case_from_report_row,
    propose_shape,
)
from lion_rebar.ui.new_shapes_dialog import NewShapesDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    съществуващо = QApplication.instance()
    return съществуващо or QApplication([])


def _ред(handle, text, L, ratios, raw, printed, angles):
    return {
        "annotation_handle": handle, "annotation_text": text,
        "declared_length_cm": L, "diameter_mm": 14.0,
        "geometry_length_raw": raw, "nearby_dimensions_cm": list(printed),
        "canonical_signature": "O|n=%d|r=%s|a=%s" % (
            len(ratios), ",".join("%.5f" % r for r in ratios), angles),
        "master_shape_code": None, "unresolved_curve_count": 0,
        "source_curve_count": 0, "candidate_handles": [],
    }


@pytest.fixture()
def предложения():
    кобилица = propose_shape([case_from_report_row(_ред(
        "6A04B1", "2x3N14x195", 195.0,
        (0.25781, 0.08750, 0.30937, 0.08750, 0.25781), 193.94,
        [17.0, 50.0, 60.0], "-45.00,45.00,45.00,-45.00"))])
    друга = propose_shape([case_from_report_row(_ред(
        "6A1033", "2x4N14x125", 125.0,
        (0.14045, 0.24785, 0.19828, 0.41322), 121.04,
        [17.0, 30.0, 24.0, 50.0], "-90.00,-45.00,45.00"))])
    return [("F021", кобилица), ("F022", друга)]


def test_името_се_предлага_само(app, предложения):
    d = NewShapesDialog(предложения)
    имена = [d.list.item(i).text() for i in range(d.list.count())]
    assert any("КОБИЛИЦА" in т for т in имена), имена
    assert d.list.count() == 2


def test_всички_са_отметнати_по_подразбиране(app, предложения):
    d = NewShapesDialog(предложения)
    избрани = d.selected()
    assert [к for к, _и, _п in избрани] == ["F021", "F022"]


def test_отметката_маха_формата(app, предложения):
    d = NewShapesDialog(предложения)
    d.list.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert [к for к, _и, _п in d.selected()] == ["F021"]


def test_празно_име_не_ражда_форма(app, предложения):
    d = NewShapesDialog(предложения)
    d.list.setCurrentRow(0)
    d.name_edit.setText("   ")
    assert [к for к, _и, _п in d.selected()] == ["F022"]


def test_обръщането_на_посоката_мени_силуета(app, предложения):
    """„Обърни посоката“ е поправката, която иначе беше клетка по клетка."""

    d = NewShapesDialog(предложения)
    d.list.setCurrentRow(0)

    _к, _и, преди = d.selected()[0]
    посоки_преди = [s.bend_direction for s in
                    build_draft_shape(преди, code="F021", name="К").segments]

    d._flip_current()

    _к, _и, след = d.selected()[0]
    посоки_след = [s.bend_direction for s in
                   build_draft_shape(след, code="F021", name="К").segments]

    assert посоки_преди != посоки_след
    размяна = {"LEFT": "RIGHT", "RIGHT": "LEFT", "NONE": "NONE"}
    assert [размяна[p] for p in посоки_преди] == посоки_след


def test_преименуването_се_вижда_в_списъка(app, предложения):
    d = NewShapesDialog(предложения)
    d.list.setCurrentRow(0)
    d.name_edit.setText("КОБИЛИЦА ЕДНА")
    assert "КОБИЛИЦА ЕДНА" in d.list.item(0).text()
    assert d.selected()[0][1] == "КОБИЛИЦА ЕДНА"
