from __future__ import annotations

import json
import logging
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDockWidget, QGraphicsSimpleTextItem

from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.engineer_decision import save_engineer_decision
from lion_rebar.project.models import ProjectModel
from lion_rebar.ui.engineer_decision_dialog import EngineerDecisionDialog
from lion_rebar.ui.main_window import MainWindow
from lion_rebar.ui.review_center import ReviewCenterDialog
from lion_rebar.ui.shape_preview import ShapePreview


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _shape(code: str, codes: tuple[str, ...], *, name: str = "форма") -> ShapeDefinition:
    directions = {
        "F003": ("НАДОЛУ", "НАДЯСНО", "НАГОРЕ"),
        "F005": ("НАЛЯВО", "НАДОЛУ", "НАДЯСНО"),
        "F007": ("НАГОРЕ", "НАДЯСНО", "НАДОЛУ_НАЛЯВО"),
    }.get(code, tuple("" for _ in codes))
    return ShapeDefinition(
        code=code,
        name=name,
        active=True,
        state="APPROVED",
        initial_direction="UP" if code == "F007" else "RIGHT",
        segments=[
            SegmentDefinition(
                index=index + 1,
                length_code=length_code,
                drawing_direction=directions[index] if index < len(directions) else "",
                bend_after_deg=90 if index < len(codes) - 1 else None,
                notes="Плавна C-образна крива." if code == "F007" and index == 2 else "",
            )
            for index, length_code in enumerate(codes)
        ],
    )


def _row(**overrides) -> dict:
    row = {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": "A1",
        "annotation_text": "29N8x94",
        "annotation_count": 29,
        "diameter_mm": 8,
        "declared_length_cm": 94,
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "matcher_disposition": "MANUAL",
        "candidate_handles": ["G1"],
        "candidate_basis": "DXF_CENTERLINE_AND_DIMENSIONS",
        "candidate_notes": ["проверими размери"],
        "suggested_shape_code": "F003",
        "suggested_segments_cm": [20, 54, 20],
        "canonical_turn_angles_deg": [90, 90],
        "data_quality_flags": [],
        "data_quality_notes": [],
        "analysis_warnings": [],
        "alternative_candidates": [],
    }
    row.update(overrides)
    return row


def test_preview_uses_only_real_values_and_links_repeated_master_parameter() -> None:
    app = _app()
    preview = ShapePreview()
    preview.resize(800, 320)
    preview.set_shape(_shape("F007", ("L1", "L2", "L1"), name="S/C форма"))
    preview.set_production_values(
        [11.5, 24, 11.5],
        sources=["надпис", "DXF", "надпис"],
        total_length_cm=47,
    )
    preview.show()
    app.processEvents()

    text = "\n".join(
        item.text()
        for item in preview.scene().items()
        if isinstance(item, QGraphicsSimpleTextItem)
    )
    assert "L1 = 11.5 cm" in text
    assert "L2 = 24 cm" in text
    assert "L = 47 cm" in text
    assert "L1 = 50" not in text
    assert len(preview._segment_items["L1"]) == 2

    preview.select_segment("L1")
    assert preview.selected_segment == "L1"
    assert all(item.pen().color() == QColor("#ea580c") for item in preview._segment_items["L1"])
    assert all(item.pen().widthF() >= 4 for item in preview._segment_items["L1"])
    preview.close()


def test_dialog_exposes_independent_and_linked_fields_bidirectionally() -> None:
    app = _app()
    f003 = _shape("F003", ("L1", "L2", "L3"), name="два винкела")
    f007 = _shape("F007", ("L1", "L2", "L1"), name="S/C форма")
    dialog = EngineerDecisionDialog(row=_row(), shapes=[f003, f007])
    dialog.show()
    app.processEvents()

    assert set(dialog.segment_edits) == {"L1", "L2", "L3"}
    dialog.shape_combo.setCurrentIndex(dialog.shape_combo.findData("F007"))
    assert set(dialog.segment_edits) == {"L1", "L2"}
    assert dialog.segment_edits["L1"].text() == ""
    dialog.segments_edit.setText("11.5 + 24 + 11.5")
    assert dialog.segment_edits["L1"].text() == "11.5"
    assert dialog.segment_edits["L2"].text() == "24"

    dialog._preview_segment_selected("L2")
    app.processEvents()
    assert dialog.segment_edits["L2"].hasFocus()
    assert dialog.shape_preview.selected_segment == "L2"

    dialog.segments_edit.setText("10 + 24 + 12")
    with pytest.raises(ValueError, match="свързан MASTER параметър"):
        dialog._values()
    dialog.close()


def test_valid_structured_shape_correction_needs_no_free_text_or_checkbox() -> None:
    _app()
    f003 = _shape("F003", ("L1", "L2", "L3"), name="два винкела")
    f005 = _shape("F005", ("L1", "L2", "L3"), name="фиба")
    dialog = EngineerDecisionDialog(row=_row(), shapes=[f003, f005])
    dialog.shape_combo.setCurrentIndex(dialog.shape_combo.findData("F005"))
    dialog.segments_edit.setText("40 + 14 + 40")
    dialog.reason_combo.setCurrentIndex(dialog.reason_combo.findData("WRONG_SHAPE"))
    dialog.note_edit.clear()
    dialog._refresh()

    values = dialog._values()
    assert values["action_class"] == "CORRECTION"
    assert not values["note_policy"].required
    assert values["note"] == ""
    assert not dialog.confirm_check.isVisible()
    assert dialog.save_button.isEnabled()
    dialog.close()


def _payload(*, reason: str = "WRONG_SHAPE", note: str = "", handles=("G1",)) -> dict:
    return {
        "reason_code": reason,
        "note": note,
        "annotation": {"handle": "A1", "text": "29N8x94", "resolved_text": "29N8x94"},
        "original": {
            "count": 29,
            "diameter_mm": 8,
            "declared_length_cm": 94,
            "shape_code": "F003",
            "segment_lengths_cm": [20, 54, 20],
            "geometry_handles": ["G1"],
        },
        "resolved": {
            "count": 29,
            "diameter_mm": 8,
            "production_length_cm": 94,
            "segment_lengths_cm": [40, 14, 40],
            "shape_code": "F005",
            "shape_name": "ФИБА",
        },
        "geometry": {
            "handles": list(handles),
            "basis": "DXF_CENTERLINE_AND_DIMENSIONS",
            "notes": ["dimension evidence"],
        },
        "context": {"engine_version": "0.3.14", "project_version": "0.1.0"},
    }


def test_persistence_accepts_traceable_correction_and_writes_exact_system_audit(tmp_path: Path) -> None:
    working = tmp_path / "working"
    working.mkdir()
    (working / "project.json").write_text(
        json.dumps(
            {
                "name": "P",
                "version": "0.1.0",
                "schema": "test",
                "engine_id": "lion-rebar-master",
                "engine_version": "0.3.14",
            }
        ),
        encoding="utf-8",
    )
    path = save_engineer_decision(tmp_path, _payload())
    decision = json.loads(path.read_text(encoding="utf-8"))["links"][0]["engineer_decision"]

    assert decision["action_class"] == "CORRECTION"
    assert decision["operator_note"] == ""
    assert decision["note_policy"]["required"] is False
    audit = decision["automatic_audit"]
    assert audit["generated_by"] == "LION_REBAR_SYSTEM"
    assert audit["before"]["shape_code"] == "F003"
    assert audit["after"]["shape_code"] == "F005"
    assert audit["changes"]["segment_lengths_cm"] == {
        "before": [20, 54, 20],
        "after": [40.0, 14.0, 40.0],
    }
    assert audit["context"]["engine_version"] == "0.3.14"
    assert audit["context"]["project_version"] == "0.1.0"


@pytest.mark.parametrize(
    "payload, message",
    [
        (_payload(reason="NO_GEOMETRY", note="   ", handles=()), "без надеждна DXF геометрия"),
        (_payload(reason="OTHER", note="\t", handles=("G1",)), "Друга"),
    ],
)
def test_persistence_rejects_blank_note_for_strict_cases(tmp_path: Path, payload: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        save_engineer_decision(tmp_path, payload)


def _write_review(path: Path) -> None:
    rows = [
        _row(annotation_handle="A1", annotation_text="29N8x94"),
        _row(annotation_handle="A2", annotation_text="8N10x120", candidate_handles=["G2"]),
    ]
    path.write_text(
        json.dumps(
            {
                "schema": "lion-rebar-geometry-batch-report/v3",
                "project_name": "P",
                "generated_at": "2026-09-05T10:00:00+03:00",
                "summary": {},
                "rows": rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_embedded_review_center_stays_visible_and_defer_does_not_decide(tmp_path: Path) -> None:
    _app()
    report = tmp_path / "report.json"
    state = tmp_path / "state.json"
    _write_review(report)
    dialog = ReviewCenterDialog(report, state_path=state, embedded=True)

    assert dialog.queue_combo.currentData() == "WORK"
    assert dialog.details.isHidden()
    assert not dialog.expert_panel.isVisible()
    before = dict(dialog._state.get("decisions", {}))
    first = dialog.current_row()["annotation_handle"]
    dialog._advance()
    assert dialog.current_row()["annotation_handle"] != first
    assert dialog._state.get("decisions", {}) == before

    dialog._preview_current()
    assert dialog.windowOpacity() == 1.0
    dialog.set_geometry_selection_active(True, "8N10x120")
    assert not dialog.selection_mode_label.isHidden()
    assert dialog.cancel_edits_button.isEnabled()
    assert "източник:" in dialog.position_summary_label.text()
    dialog.close()


class _RepositoryStub:
    def load_current(self) -> EnginePackage:
        return EnginePackage(
            engine_id="test-engine",
            title="Test",
            version="0.3.14",
            production_ready=True,
        )


class _SessionStub:
    has_active_session = False

    def active_summary(self):
        return None

    def record_system_event(self, *_args, **_kwargs) -> None:
        return None


def test_main_window_hosts_review_center_in_adjustable_persistent_dock(tmp_path: Path) -> None:
    app = _app()
    report = tmp_path / "report.json"
    _write_review(report)
    window = MainWindow(
        _RepositoryStub(),
        logging.getLogger("rc38-dock-test"),
        session_service=_SessionStub(),
    )
    window._ui_settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    window.project = ProjectModel(name="P", root=str(tmp_path))
    window.import_result = object()
    window.scene_result = object()
    window._review_report_matches_current_project = lambda _path: True
    window._read_all_approved_geometry_links = lambda: {}
    window._schedule_ai_diagnostic_bundle = lambda: None
    window.resize(1366, 768)
    window.show()
    app.processEvents()

    window.open_review_center(report)
    app.processEvents()

    assert isinstance(window._review_dock, QDockWidget)
    assert window._review_dock.widget() is window._review_center
    assert window._review_center.embedded is True
    assert window.dockWidgetArea(window._review_dock) == window.dockWidgetArea(window.entity_dock)
    assert window.centralWidget() is window.drawing_view
    assert window._review_dock.width() >= 360

    window._save_review_dock_state(window._review_dock)
    assert int(window._ui_settings.value("review_center/width")) >= 360
    window.scene_result = None
    window._close_review_center()
    app.processEvents()
    window.project = None
    window.close()
