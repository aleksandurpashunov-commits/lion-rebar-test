from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QRect, QSettings, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition
from lion_rebar.ui.engineer_decision_dialog import EngineerDecisionDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _shape() -> ShapeDefinition:
    return ShapeDefinition(
        code="F005",
        name="Фиба",
        active=True,
        state="APPROVED",
        segments=[
            SegmentDefinition(index=1, length_code="L1", bend_after_deg=90),
            SegmentDefinition(index=2, length_code="L2", bend_after_deg=90),
            SegmentDefinition(index=3, length_code="L3"),
        ],
    )


def _row() -> dict:
    return {
        "annotation_handle": "A1",
        "annotation_text": "29N8x94",
        "annotation_count": 29,
        "diameter_mm": 8,
        "declared_length_cm": 94,
        "candidate_handles": ["G1"],
        "candidate_basis": "DXF_CENTERLINE_AND_DIMENSIONS",
        "suggested_shape_code": "F005",
        "suggested_segments_cm": [40, 14, 40],
        "canonical_turn_angles_deg": [90, 90],
    }


def test_compact_dialog_has_scrollable_body_and_sticky_actions() -> None:
    app = _app()
    dialog = EngineerDecisionDialog(row=_row(), shapes=[_shape()])
    dialog.resize(dialog.minimumWidth(), dialog.minimumHeight())
    dialog.show()
    app.processEvents()

    assert dialog.minimumWidth() <= 560
    assert dialog.minimumHeight() <= 420
    assert dialog.width() <= dialog.screen().availableGeometry().width()
    assert dialog.height() <= dialog.screen().availableGeometry().height()
    assert dialog.scroll_area.widgetResizable()
    assert (
        dialog.scroll_area.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert not dialog.scroll_area.isAncestorOf(dialog.button_box)
    assert dialog.button_box.isVisible()
    assert dialog.save_button.isVisible()
    assert dialog.cancel_button.isVisible()
    assert dialog.shape_preview.minimumHeight() == 150
    assert dialog.shape_preview.maximumHeight() == 200
    dialog.close()


def test_initial_size_fits_1366x768_screen_at_150_percent_scaling() -> None:
    _app()

    class LaptopDialog(EngineerDecisionDialog):
        def _available_screen_geometry(self):
            # Qt reports device-independent pixels: 1366x768 at 150% scaling,
            # with the Windows taskbar reserved, is approximately 911x480.
            return QRect(0, 0, 911, 480)

    dialog = LaptopDialog(row=_row(), shapes=[_shape()])
    assert dialog.width() <= 911 - dialog._SCREEN_MARGIN * 2
    assert dialog.height() <= 480 - dialog._SCREEN_MARGIN * 2
    assert dialog.minimumWidth() <= dialog.width()
    assert dialog.minimumHeight() <= dialog.height()
    dialog.close()


def test_keyboard_shortcuts_exist_and_save_cannot_bypass_disabled_gate() -> None:
    _app()
    dialog = EngineerDecisionDialog(row=_row(), shapes=[_shape()])
    portable_keys = {
        shortcut.key().toString(QKeySequence.SequenceFormat.PortableText)
        for shortcut in dialog.findChildren(QShortcut)
    }
    assert "Ctrl+Return" in portable_keys
    assert "Ctrl+Enter" in portable_keys
    assert "Esc" in portable_keys

    calls: list[str] = []
    dialog._accept = lambda: calls.append("accepted")
    dialog.save_button.setEnabled(False)
    dialog._accept_if_ready()
    assert calls == []
    dialog.save_button.setEnabled(True)
    dialog._accept_if_ready()
    assert calls == ["accepted"]
    dialog.close()


def test_dialog_geometry_uses_main_window_settings_store(tmp_path) -> None:
    _app()
    parent = QWidget()
    parent._ui_settings = QSettings(
        str(tmp_path / "ui.ini"),
        QSettings.Format.IniFormat,
    )
    dialog = EngineerDecisionDialog(row=_row(), shapes=[_shape()], parent=parent)
    dialog.resize(620, 470)
    dialog.move(40, 50)
    dialog.done(QDialog.DialogCode.Rejected)

    stored = parent._ui_settings.value(dialog._GEOMETRY_SETTINGS_KEY)
    assert stored is not None

    restored = EngineerDecisionDialog(row=_row(), shapes=[_shape()], parent=parent)
    assert restored.size() == dialog.size()
    restored.close()
    parent.close()
