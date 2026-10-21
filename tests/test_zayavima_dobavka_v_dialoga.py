"""Полето за заявена добавка в редактора на инженерното решение.

Проверява се ПОВЕДЕНИЕ, не текст на кода: празно поле отказва както преди,
точното число пуска позицията, сгрешеното число казва защо не минава, а
записаното решение носи числото.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition
from lion_rebar.ui.engineer_decision_dialog import EngineerDecisionDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _forma() -> ShapeDefinition:
    """F007 с три участъка: двете крайни рамена са еднакви."""

    return ShapeDefinition(
        code="F007",
        name="S за продънване",
        active=True,
        state="APPROVED",
        segments=[
            SegmentDefinition(index=1, length_code="L1", bend_after_deg=90),
            SegmentDefinition(index=2, length_code="L2", bend_after_deg=90),
            SegmentDefinition(index=3, length_code="L1"),
        ],
    )


def _red() -> dict:
    """13 + 18 + 13 = 44 при надписани 47 — недостиг точно 3 cm."""

    return {
        "annotation_handle": "A1",
        "annotation_text": "12N10x47",
        "annotation_count": 12,
        "diameter_mm": 10,
        "declared_length_cm": 47,
        "candidate_handles": ["G1"],
        "candidate_basis": "DXF_CENTERLINE_AND_DIMENSIONS",
        "suggested_shape_code": "F007",
        "suggested_segments_cm": [13, 18, 13],
        "canonical_turn_angles_deg": [90, 90],
    }


def _диалог() -> EngineerDecisionDialog:
    _app()
    диалог = EngineerDecisionDialog(row=_red(), shapes=[_forma()])
    диалог.note_edit.setPlainText("Детайлът е проверен по отпечатаните коти.")
    return диалог


def test_prazno_pole_otkazva_kakto_predi() -> None:
    диалог = _диалог()

    with pytest.raises(ValueError) as грешка:
        диалог._values()

    assert "трябва да е 3 cm" in str(грешка.value)


def test_tochnoto_chislo_pusca_pozitsiyata() -> None:
    диалог = _диалог()
    диалог.bend_allowance_edit.setText("3")

    стойности = диалог._values()

    assert стойности["bend_allowance_cm"] == pytest.approx(3.0)
    assert стойности["shape_code"] == "F007"


def test_pogreshnoto_chislo_kazva_zashto_ne_minava() -> None:
    диалог = _диалог()
    диалог.bend_allowance_edit.setText("5")

    with pytest.raises(ValueError) as грешка:
        диалог._values()

    assert "не затваря уравнението" in str(грешка.value)


def test_pregledut_pokazva_che_e_valid_no_sas_zayavka() -> None:
    диалог = _диалог()

    без = диалог._preview_status((13.0, 18.0, 13.0), 47.0)
    assert "НЕВАЛИДНО" in без

    диалог.bend_allowance_edit.setText("3")
    със = диалог._preview_status((13.0, 18.0, 13.0), 47.0)
    assert "валидно със заявена добавка 3 cm" in със


def test_chisloto_vliza_v_zapisvaniya_payload() -> None:
    диалог = _диалог()
    диалог.bend_allowance_edit.setText("3")

    диалог._accept()

    assert диалог.result_payload is not None
    assert диалог.result_payload["resolved"]["bend_allowance_cm"] == pytest.approx(3.0)
