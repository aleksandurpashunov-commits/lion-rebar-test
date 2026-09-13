from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsSimpleTextItem,
    QGraphicsTextItem,
)

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.ui.drawing_view import (
    DISPLAY_ONLY_ROLE,
    DISPLAY_SOURCE_ID_ROLE,
    DrawingView,
    build_scene,
    centerline_path_for_record,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _record(
    handle: str,
    kind: str,
    geometry: dict,
    *,
    layer: str = "ARM",
    text: str | None = None,
    raw: dict | None = None,
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type=kind,
        layer=layer,
        geometry=geometry,
        text=text,
        raw=dict(raw or {}),
    )


def _has_curve(path: QPainterPath) -> bool:
    return any(
        path.elementAt(index).type == QPainterPath.ElementType.CurveToElement
        for index in range(path.elementCount())
    )


def test_display_stream_is_visible_layered_fitted_but_never_engineering_selectable() -> None:
    _app()
    engineering = _record(
        "ENG",
        "LINE",
        {"start": [0.0, 0.0, 0.0], "end": [10.0, 0.0, 0.0]},
    )
    display = _record(
        "DISPLAY-TEXT-HANDLE",
        "TEXT",
        {"insert": [1000.0, 200.0, 0.0], "height": 12.0, "rotation": 30.0},
        layer="arm_shaibi",
        text="proxy text",
        raw={
            "display_id": "MS/I1/P77/V3",
            "proxy_source_key": ["I1", "P77", 3, "TEXT"],
            "parent_proxy_handle": "P77",
            "proxy_instance_id": "I1",
            "display_rgb": [255, 0, 0],
        },
    )

    built = build_scene([engineering], [display])

    assert set(built.handle_to_record) == {"ENG"}
    assert set(built.handle_to_item) == {"ENG"}
    assert "DISPLAY-TEXT-HANDLE" not in built.handle_to_record
    assert set(built.display_id_to_item) == {"MS/I1/P77/V3"}
    display_item = built.display_id_to_item["MS/I1/P77/V3"]
    assert isinstance(display_item, QGraphicsSimpleTextItem)
    assert display_item.data(DISPLAY_SOURCE_ID_ROLE) == "MS/I1/P77/V3"
    assert display_item.data(DISPLAY_ONLY_ROLE) is True
    assert not (display_item.flags() & QGraphicsItem.ItemIsSelectable)
    assert display_item.acceptedMouseButtons() == Qt.MouseButton.NoButton
    assert display_item.rotation() == pytest.approx(-30.0)
    assert display_item.boundingRect().height() * display_item.scale() == pytest.approx(12.0)
    assert display_item.brush().color().name() == "#ff0000"
    assert display_item in built.layer_groups["arm_shaibi"]
    assert built.scene.itemsBoundingRect().right() > 1000.0

    # Resetting the normal engineering selection filter cannot accidentally
    # opt a display-only proxy item into selection.
    view = DrawingView()
    view.setScene(built.scene)
    view.set_selection_entity_filter(None)
    assert not (display_item.flags() & QGraphicsItem.ItemIsSelectable)

    for item in built.layer_groups["arm_shaibi"]:
        item.setVisible(False)
    assert not display_item.isVisible()
    view.close()


def test_recognized_proxy_annotation_preserves_each_source_text_fragment() -> None:
    _app()
    annotation = _record(
        "PXANN-P77-1",
        "BLOCK_ATTR_TEXT",
        {
            "insert": [100.0, 50.0, 0.0],
            "height": 8.0,
            "rotation": 0.0,
            "render_fragments": [
                {
                    "text": "12",
                    "insert": [100.0, 50.0, 0.0],
                    "height": 4.0,
                    "rotation": 15.0,
                    "virtual_index": 7,
                    "color_index": 256,
                    "display_rgb": "#123456",
                    "linetype": "BYLAYER",
                },
                {
                    "text": "N18",
                    "insert": [130.0, 65.0, 0.0],
                    "height": 9.0,
                    "rotation": -30.0,
                    "virtual_index": 8,
                    "color_index": 256,
                    "display_rgb": "#0000FF",
                    "linetype": "BYLAYER",
                },
            ],
        },
        text="12 N18",
        raw={"proxy_source_key": "MS/P77/annotation/1"},
    )

    built = build_scene([annotation])

    assert set(built.handle_to_item) == {"PXANN-P77-1"}
    item = built.handle_to_item["PXANN-P77-1"]
    assert isinstance(item, QGraphicsItemGroup)
    assert item.flags() & QGraphicsItem.ItemIsSelectable
    children = item.childItems()
    assert len(children) == 2
    assert all(isinstance(child, QGraphicsSimpleTextItem) for child in children)
    by_text = {child.text(): child for child in children}
    assert set(by_text) == {"12", "N18"}
    assert (by_text["12"].x(), by_text["12"].y()) == pytest.approx((100.0, -50.0))
    assert by_text["12"].rotation() == pytest.approx(-15.0)
    assert by_text["12"].boundingRect().height() * by_text["12"].scale() == pytest.approx(4.0)
    assert by_text["12"].brush().color().name() == "#123456"
    assert (by_text["N18"].x(), by_text["N18"].y()) == pytest.approx((130.0, -65.0))
    assert by_text["N18"].rotation() == pytest.approx(30.0)
    assert by_text["N18"].boundingRect().height() * by_text["N18"].scale() == pytest.approx(9.0)
    assert by_text["N18"].brush().color().name() == "#0000ff"

    display_built = build_scene([], [annotation])
    display_group = display_built.display_id_to_item["PXANN-P77-1"]
    assert isinstance(display_group, QGraphicsItemGroup)
    assert not (display_group.flags() & QGraphicsItem.ItemIsSelectable)
    for child in display_group.childItems():
        assert not (child.flags() & QGraphicsItem.ItemIsSelectable)
        assert child.acceptedMouseButtons() == Qt.MouseButton.NoButton


def test_exact_proxy_source_key_prevents_double_draw_but_not_missing_fallback() -> None:
    _app()
    shared_key = {"instance": "MS", "path": ["P1", 0, "LINE"]}
    engineering = _record(
        "PXG-P1-0",
        "LINE",
        {"start": [0.0, 0.0], "end": [10.0, 0.0]},
        raw={"source_key": shared_key, "display_rgb": "#D600D6"},
    )
    duplicate_display = _record(
        "DUP",
        "LINE",
        {"start": [100.0, 0.0], "end": [110.0, 0.0]},
        raw={"display_id": "duplicate", "source_key": shared_key},
    )
    unsupported_parent = _record(
        "PROXY-P2",
        "ACAD_PROXY_ENTITY",
        {"unsupported_for_render": True},
        raw={"source_key": "P2/V0"},
    )
    usable_fallback = _record(
        "DISPLAY-P2",
        "LINE",
        {"start": [200.0, 0.0], "end": [210.0, 0.0]},
        raw={"display_id": "P2/V0", "source_key": "P2/V0"},
    )
    repeated_fallback = _record(
        "DISPLAY-P2",
        "LINE",
        {"start": [300.0, 0.0], "end": [310.0, 0.0]},
        raw={"display_id": "P2/V0", "source_key": "P2/V0"},
    )

    built = build_scene(
        [engineering, unsupported_parent],
        [duplicate_display, usable_fallback, repeated_fallback],
    )

    assert "duplicate" not in built.display_id_to_item
    assert set(built.display_id_to_item) == {"P2/V0"}
    assert len(built.scene.items()) == 2
    assert built.scene.itemsBoundingRect().right() < 220.0
    assert set(built.handle_to_item) == {"PXG-P1-0"}
    assert set(built.handle_to_record) == {"PXG-P1-0", "PROXY-P2"}
    assert built.handle_to_item["PXG-P1-0"].pen().color().name() == "#d600d6"


def test_text_center_right_fit_and_aligned_anchors_use_dxf_alignment_points() -> None:
    _app()
    center = _record(
        "CENTER",
        "TEXT",
        {
            "insert": [0.0, 0.0, 0.0],
            "align_point": [100.0, 50.0, 0.0],
            "height": 10.0,
            "rotation": 0.0,
            "halign": 1,
            "valign": 2,
            "width_factor": 1.5,
        },
        text="CENTER",
    )
    right = _record(
        "RIGHT",
        "TEXT",
        {
            "insert": [0.0, 0.0, 0.0],
            "align_point": [220.0, 80.0, 0.0],
            "height": 8.0,
            "rotation": 0.0,
            "halign": 2,
            "valign": 3,
        },
        text="RIGHT",
    )
    fit = _record(
        "FIT",
        "TEXT",
        {
            "insert": [0.0, 150.0, 0.0],
            "align_point": [120.0, 150.0, 0.0],
            "height": 10.0,
            "rotation": 45.0,  # two-point direction is authoritative
            "halign": 5,
            "valign": 0,
        },
        text="FIT TEXT",
    )
    aligned = _record(
        "ALIGNED",
        "TEXT",
        {
            "insert": [200.0, 150.0, 0.0],
            "align_point": [200.0, 250.0, 0.0],
            "height": 2.0,
            "rotation": 0.0,
            "halign": 3,
            "valign": 0,
        },
        text="ALIGNED",
    )

    built = build_scene([center, right, fit, aligned])

    center_item = built.handle_to_item["CENTER"]
    assert isinstance(center_item, QGraphicsSimpleTextItem)
    center_anchor = center_item.mapToScene(center_item.boundingRect().center())
    assert (center_anchor.x(), center_anchor.y()) == pytest.approx((100.0, -50.0))
    assert center_item.transform().m11() == pytest.approx(1.5)

    right_item = built.handle_to_item["RIGHT"]
    assert isinstance(right_item, QGraphicsSimpleTextItem)
    right_bounds = right_item.boundingRect()
    right_anchor = right_item.mapToScene(right_bounds.topRight())
    assert (right_anchor.x(), right_anchor.y()) == pytest.approx((220.0, -80.0))

    fit_item = built.handle_to_item["FIT"]
    assert isinstance(fit_item, QGraphicsSimpleTextItem)
    fit_bounds = fit_item.boundingRect()
    fit_start = fit_item.mapToScene(QPointF(fit_bounds.left(), fit_bounds.center().y()))
    fit_end = fit_item.mapToScene(QPointF(fit_bounds.right(), fit_bounds.center().y()))
    assert (fit_start.x(), fit_start.y()) == pytest.approx((0.0, -150.0))
    assert (fit_end.x(), fit_end.y()) == pytest.approx((120.0, -150.0))
    assert fit_item.rotation() == pytest.approx(0.0)
    assert fit_item.boundingRect().height() * fit_item.scale() == pytest.approx(10.0)

    aligned_item = built.handle_to_item["ALIGNED"]
    assert isinstance(aligned_item, QGraphicsSimpleTextItem)
    aligned_bounds = aligned_item.boundingRect()
    aligned_start = aligned_item.mapToScene(
        QPointF(aligned_bounds.left(), aligned_bounds.center().y())
    )
    aligned_end = aligned_item.mapToScene(
        QPointF(aligned_bounds.right(), aligned_bounds.center().y())
    )
    assert (aligned_start.x(), aligned_start.y()) == pytest.approx((200.0, -150.0))
    assert (aligned_end.x(), aligned_end.y()) == pytest.approx((200.0, -250.0))
    assert aligned_item.rotation() == pytest.approx(-90.0)


def test_mtext_attachment_width_and_text_direction_are_preserved() -> None:
    _app()
    mtext = _record(
        "MTEXT",
        "MTEXT",
        {
            "insert": [300.0, 200.0, 0.0],
            "char_height": 10.0,
            "rotation": 0.0,
            "width": 80.0,
            "attachment_point": 3,
            "text_direction": [0.0, 1.0, 0.0],
        },
        text="LONG MTEXT VALUE THAT WRAPS",
    )

    built = build_scene([mtext])
    item = built.handle_to_item["MTEXT"]

    assert isinstance(item, QGraphicsTextItem)
    assert item.rotation() == pytest.approx(-90.0)
    assert item.textWidth() * item.scale() == pytest.approx(80.0)
    anchor = item.mapToScene(item.boundingRect().topRight())
    assert (anchor.x(), anchor.y()) == pytest.approx((300.0, -200.0))


def test_polyline_closing_bulge_and_arc_use_curves_in_scene_and_highlight() -> None:
    _app()
    polyline = _record(
        "BULGED",
        "LWPOLYLINE",
        {
            "points": [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 10.0, 1.0]],
            "bulges": [0.0, 0.0, 1.0],
            "closed": True,
        },
    )
    arc = _record(
        "ARC",
        "ARC",
        {"center": [30.0, 0.0, 0.0], "radius": 10.0, "start_angle": 0.0, "end_angle": 90.0},
    )

    built = build_scene([polyline, arc])
    polyline_item = built.handle_to_item["BULGED"]
    arc_item = built.handle_to_item["ARC"]
    assert isinstance(polyline_item, QGraphicsPathItem)
    assert isinstance(arc_item, QGraphicsPathItem)
    assert _has_curve(polyline_item.path())
    assert _has_curve(arc_item.path())

    # A straight-chord triangle would stop at x=0. The bulged closing edge
    # extends beyond it, proving that the last-vertex bulge was not discarded.
    assert polyline_item.path().boundingRect().left() < -2.0
    assert arc_item.path().boundingRect().height() == pytest.approx(10.0)

    highlight_polyline = centerline_path_for_record(polyline)
    highlight_arc = centerline_path_for_record(arc)
    assert highlight_polyline is not None and _has_curve(highlight_polyline)
    assert highlight_arc is not None and _has_curve(highlight_arc)
    assert highlight_polyline.boundingRect() == polyline_item.path().boundingRect()
    assert highlight_arc.boundingRect() == arc_item.path().boundingRect()


def test_display_rgb_is_preserved_when_readable_and_darkened_when_too_light() -> None:
    _app()
    red = _record(
        "RED",
        "LINE",
        {"start": [0.0, 0.0], "end": [10.0, 0.0]},
        raw={"display_id": "red", "display_rgb": [255, 0, 0]},
    )
    white = _record(
        "WHITE",
        "LINE",
        {"start": [0.0, 10.0], "end": [10.0, 10.0]},
        raw={"display_id": "white", "display_rgb": {"r": 255, "g": 255, "b": 255}},
    )
    invalid = _record(
        "INVALID",
        "LINE",
        {"start": [0.0, 20.0], "end": [10.0, 20.0]},
        raw={"display_id": "invalid", "display_rgb": [999, 0, 0]},
    )

    built = build_scene([], [red, white, invalid])
    assert isinstance(built.display_id_to_item["red"], QGraphicsLineItem)
    assert built.display_id_to_item["red"].pen().color().name() == "#ff0000"
    assert built.display_id_to_item["white"].pen().color().name() == "#8c8c8c"
    assert built.display_id_to_item["invalid"].pen().color().name() == "#202938"


def test_ellipse_and_extractor_render_points_extend_proxy_display_support() -> None:
    _app()
    ellipse = _record(
        "ELLIPSE-DISPLAY",
        "ELLIPSE",
        {
            "center": [5.0, 6.0, 0.0],
            "major_axis": [10.0, 0.0, 0.0],
            "ratio": 0.5,
            "start_param": 0.0,
            "end_param": math.tau,
        },
        raw={"display_id": "ellipse"},
    )
    spline = _record(
        "SPLINE-DISPLAY",
        "SPLINE",
        {
            # These are display points produced by the importer from ezdxf's
            # spline flattener, not the (incorrect-to-connect) control polygon.
            "render_points": [[20.0, 0.0], [25.0, 5.0], [30.0, 0.0]],
            "closed": False,
        },
        raw={"display_id": "spline"},
    )

    built = build_scene([], [ellipse, spline])
    ellipse_item = built.display_id_to_item["ellipse"]
    spline_item = built.display_id_to_item["spline"]
    assert isinstance(ellipse_item, QGraphicsPathItem)
    assert isinstance(spline_item, QGraphicsPathItem)
    assert _has_curve(ellipse_item.path())
    assert ellipse_item.path().boundingRect().getCoords() == pytest.approx(
        (-5.0, -11.0, 15.0, -1.0)
    )
    assert spline_item.path().boundingRect().getCoords() == pytest.approx(
        (20.0, -5.0, 30.0, 0.0)
    )
