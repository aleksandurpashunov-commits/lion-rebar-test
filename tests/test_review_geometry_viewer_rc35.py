from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import QApplication, QGraphicsItem

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition
from lion_rebar.ui.drawing_view import (
    ENTITY_TYPE_ROLE,
    MANUAL_CENTERLINE_ENTITY_TYPES,
    DrawingView,
    build_scene,
    centerline_path_for_record,
)
from lion_rebar.ui.engineer_decision_dialog import EngineerDecisionDialog
from lion_rebar.ui.main_window import (
    _manual_geometry_exceeds_declared_length,
    _shape_dimension_overlay_input,
    _shape_dimension_overlay_text,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _record(handle: str, kind: str, geometry: dict) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type=kind,
        layer="ARM",
        geometry=geometry,
    )


def test_hit_test_prefers_rebar_centreline_over_overlapping_dimension() -> None:
    app = _app()
    line = _record("BAR", "LINE", {"start": [-20, 0, 0], "end": [20, 0, 0]})
    dimension = _record(
        "DIM",
        "DIMENSION",
        {
            "defpoint2": [-20, 0, 0],
            "defpoint3": [20, 0, 0],
            "text_midpoint": [0, 0, 0],
            "measurement": 40,
        },
    )
    built = build_scene([line, dimension])
    view = DrawingView()
    view.resize(640, 480)
    view.setScene(built.scene)
    view.setSceneRect(QRectF(-100, -100, 200, 200))
    view.fitInView(view.sceneRect())
    view.show()
    app.processEvents()

    hit = view._find_selectable_item_near(view.mapFromScene(QPointF(0, 0)))

    assert hit is not None
    assert hit.data(ENTITY_TYPE_ROLE) == "LINE"
    view.close()


def test_manual_filter_keeps_dimensions_visible_but_not_selectable() -> None:
    _app()
    line = _record("BAR", "LINE", {"start": [0, 0, 0], "end": [10, 0, 0]})
    arc = _record(
        "ARC",
        "ARC",
        {"center": [0, 0, 0], "radius": 10, "start_angle": 0, "end_angle": 90},
    )
    dimension = _record(
        "DIM",
        "DIMENSION",
        {"defpoint2": [0, 0, 0], "defpoint3": [10, 0, 0], "measurement": 10},
    )
    built = build_scene([line, arc, dimension])
    view = DrawingView()
    view.setScene(built.scene)

    view.set_selection_entity_filter(MANUAL_CENTERLINE_ENTITY_TYPES)

    assert built.handle_to_item["DIM"].isVisible()
    assert not (
        built.handle_to_item["DIM"].flags() & QGraphicsItem.ItemIsSelectable
    )
    assert built.handle_to_item["BAR"].flags() & QGraphicsItem.ItemIsSelectable
    assert built.handle_to_item["ARC"].flags() & QGraphicsItem.ItemIsSelectable

    view.set_selection_entity_filter(None)
    assert built.handle_to_item["DIM"].flags() & QGraphicsItem.ItemIsSelectable
    view.close()


def test_centreline_path_never_returns_dimension_bounds() -> None:
    dimension = _record(
        "DIM",
        "DIMENSION",
        {
            "defpoint2": [-10000, 0, 0],
            "defpoint3": [10000, 0, 0],
            "measurement": 20000,
        },
    )
    arc = _record(
        "ARC",
        "ARC",
        {"center": [0, 0, 0], "radius": 10, "start_angle": 0, "end_angle": 90},
    )

    assert centerline_path_for_record(dimension) is None
    path = centerline_path_for_record(arc)
    assert path is not None
    assert not path.isEmpty()
    assert path.boundingRect().width() <= 10.01
    assert path.boundingRect().height() <= 10.01


def test_db80_outline_length_is_reference_only_for_db88_f007() -> None:
    assert _manual_geometry_exceeds_declared_length(450.891585, 47)
    assert not _manual_geometry_exceeds_declared_length(49.0, 47)


def test_readable_overlay_is_limited_to_f006_and_f007() -> None:
    assert _shape_dimension_overlay_text("F006", [10, 25, 20], 115) == (
        "F006 | 25 × 20 cm | куки 10 cm | L=115 cm"
    )
    assert _shape_dimension_overlay_text(
        "F006", [10, 20, 165, 20, 165, 10], 395
    ) == "F006 | 165 × 20 cm | куки 10 cm | L=395 cm"
    assert _shape_dimension_overlay_text("F007", [11.5, 24, 11.5], 47) == (
        "F007 | рамена 11.5 / 11.5 cm | среда 24 cm | L=47 cm"
    )
    assert _shape_dimension_overlay_text("F005", [40, 14, 40], 94) is None


def test_f006_suggestion_schema_is_normalized_without_changing_precedence() -> None:
    values, label = _shape_dimension_overlay_input(
        "F006",
        None,
        None,
        [25, 20, 10],
    )
    assert values == (10.0, 25.0, 20.0)
    assert label == "ПРЕДЛОЖЕНИЕ — потвърди"
    assert _shape_dimension_overlay_text("F006", values, 115) == (
        "F006 | 25 × 20 cm | куки 10 cm | L=115 cm"
    )

    production, production_label = _shape_dimension_overlay_input(
        "F006",
        [5, 15, 55, 15, 55, 5],
        [10, 45, 20, 45, 20, 10],
        [25, 20, 10],
    )
    assert production == [5, 15, 55, 15, 55, 5]
    assert production_label is None


def test_explicit_f007_without_geometry_uses_strict_reason_gate() -> None:
    _app()
    f007 = ShapeDefinition(
        code="F007",
        name="S/C форма",
        active=True,
        symmetric=True,
        segments=[
            SegmentDefinition(index=1, length_code="L1", bend_after_deg=90),
            SegmentDefinition(index=2, length_code="L2", bend_after_deg=90),
            SegmentDefinition(index=3, length_code="L1"),
        ],
    )
    dialog = EngineerDecisionDialog(
        row={
            "annotation_handle": "DB88::BJ-FI-L",
            "annotation_text": "2170N10x47",
            "annotation_count": 2170,
            "diameter_mm": 10,
            "declared_length_cm": 47,
            "candidate_handles": [],
            "force_geometryless_engineer_decision": True,
        },
        shapes=[f007],
        existing_link={"geometry_handles": ["STALE-HANDLE"]},
        problem_decision={
            "decision": "NON_PRODUCTION_POSITION",
            "reason_code": "NON_PRODUCTION_POSITION",
            "reason": "Друг проблем",
            "correct_specification": "Това е F007, а не изключена позиция.",
        },
    )
    assert dialog.reason_combo.currentData() == "NON_PRODUCTION_POSITION"
    dialog.shape_combo.setCurrentIndex(dialog.shape_combo.findData("F007"))
    dialog.segments_edit.setText("11.5 + 24 + 11.5")
    dialog.reason_combo.setCurrentIndex(dialog.reason_combo.findData("NO_GEOMETRY"))
    dialog.note_edit.setPlainText("Производствената ос липсва; h=24 е проверено от детайла.")

    values = dialog._values()
    assert values["shape_code"] == "F007"
    assert values["segments"] == pytest.approx((11.5, 24.0, 11.5))
    assert values["handles"] == []
    assert dialog._available_handles == []
    assert not dialog.use_candidate_check.isChecked()
    assert not dialog.use_candidate_check.isEnabled()
    assert values["action_class"] != "CONFIRMATION"
    assert "Няма сигурна производствена ос" in dialog.geometry_notice_label.text()

    dialog.reason_combo.setCurrentIndex(
        dialog.reason_combo.findData("CONFIRM_CORRECT")
    )
    with pytest.raises(ValueError, match="Без DXF връзка"):
        dialog._values()
    dialog.close()
