from __future__ import annotations

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QWidget,
)

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import EnginePackage
from lion_rebar.manual_assignment import SUPPORTED_MANUAL_GEOMETRY
from lion_rebar.ui.drawing_view import build_scene
from lion_rebar.ui.main_window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


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


def _record(
    handle: str,
    kind: str,
    geometry: dict,
    *,
    text: str | None = None,
    layer: str = "ARM",
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type=kind,
        layer=layer,
        geometry=geometry,
        text=text,
    )


def _window_with_h3_like_scene(
    *,
    long_rejected_path: bool = False,
) -> tuple[MainWindow, object]:
    annotation = _record(
        "7AC5::BJ-FI-L",
        "BLOCK_ATTR_TEXT",
        {"insert": [3830.57, 5137.64, 0.0], "height": 13.5},
        text="12N20x170",
        layer="ARMTXT",
    )
    rejected = _record(
        "7AA0",
        "LWPOLYLINE",
        {
            "points": [
                [3978.50, 5169.23, 0.0],
                [4043.50, 5169.23, 0.0],
                [
                    9000.0 if long_rejected_path else 4043.50,
                    5189.23,
                    0.0,
                ],
            ],
            "bulges": [0.0, 0.0, 0.0],
            "closed": False,
        },
        layer="Stremena",
    )
    # A remote object makes a whole-sheet fit visibly different from the
    # intended annotation-local review view.
    remote = _record(
        "REMOTE",
        "LINE",
        {"start": [9000.0, 9000.0, 0.0], "end": [9200.0, 9000.0, 0.0]},
    )
    built = build_scene([annotation, rejected, remote])
    window = MainWindow(
        _RepositoryStub(),
        logging.getLogger("rc42-no-candidate-localization"),
        session_service=_SessionStub(),
    )
    window.scene_result = built
    window.drawing_view.setScene(built.scene)
    window.resize(1200, 800)
    window.show()
    _app().processEvents()
    return window, built


def _h3_row(*, include_rejected: bool) -> dict:
    row = {
        "annotation_handle": "7AC5::BJ-FI-L",
        "annotation_text": "12N20x170",
        "declared_length_cm": 170.0,
        "candidate_handles": [],
        "matcher_blocker_codes": [
            "NO_CANDIDATE",
            "NO_MASTER_MATCH",
            "LENGTH_NOT_COMPARABLE",
        ],
        "review_reason_codes": ["FOREIGN_GEOMETRY_REJECTED"],
        "recognition_policy": {
            "min_search_distance_raw": 55.0,
            "max_search_distance_raw": 90.0,
            "search_distance_ratio": 0.25,
            "extended_search_distance_raw": 240.0,
            "extended_search_distance_ratio": 0.60,
        },
    }
    if include_rejected:
        row["ownership_rejected_candidate"] = {
            "handles": ["7AA0"],
            "layer": "Stremena",
        }
    return row


def test_h3_no_candidate_overlay_survives_centreline_selection_filter() -> None:
    app = _app()
    window, built = _window_with_h3_like_scene()
    annotation_item = built.handle_to_item["7AC5::BJ-FI-L"]

    window._review_locate_annotation(_h3_row(include_rejected=True))
    app.processEvents()

    assert window._manual_selection_row is not None
    assert window.drawing_view._selection_entity_filter == frozenset(
        str(value).upper() for value in SUPPORTED_MANUAL_GEOMETRY
    )
    # This is expected: manual mode must not allow the annotation itself to be
    # selected as production geometry.
    assert not annotation_item.isSelected()
    assert len(window._geometry_highlights) == 3

    marker, search_zone, rejected = window._geometry_highlights
    assert isinstance(marker, QGraphicsRectItem)
    assert isinstance(search_zone, QGraphicsRectItem)
    assert isinstance(rejected, QGraphicsPathItem)
    assert marker.pen().color().name() == "#e00000"
    assert search_zone.pen().color().name() == "#0284c7"
    assert search_zone.pen().style() == Qt.PenStyle.DashLine
    assert rejected.pen().color().name() == "#d97706"
    assert rejected.pen().style() == Qt.PenStyle.DashLine
    assert all(item.scene() is built.scene for item in window._geometry_highlights)
    assert all(not item.isSelected() for item in window._geometry_highlights)
    assert not built.handle_to_item["7AA0"].isSelected()

    annotation_rect = annotation_item.sceneBoundingRect()
    assert marker.rect().contains(annotation_rect)
    # H-3: max(primary=55, 170*0.60)=102 raw units.
    assert search_zone.rect().width() == pytest.approx(
        annotation_rect.width() + 204.0
    )
    assert search_zone.rect().height() == pytest.approx(
        annotation_rect.height() + 204.0
    )

    # Re-applying the entity filter used by manual mode must not remove or
    # deselect the independent overlay objects.
    overlays = tuple(window._geometry_highlights)
    window.drawing_view.set_selection_entity_filter(SUPPORTED_MANUAL_GEOMETRY)
    app.processEvents()
    assert tuple(window._geometry_highlights) == overlays
    assert all(item.scene() is built.scene for item in overlays)

    visible_rect = window.drawing_view.mapToScene(
        window.drawing_view.viewport().rect()
    ).boundingRect()
    assert visible_rect.intersects(search_zone.rect())
    assert visible_rect.width() < built.scene.itemsBoundingRect().width() / 3.0
    assert "локалната зона" in window.statusBar().currentMessage()
    assert "не е одобрена" in window.statusBar().currentMessage()

    window._cancel_manual_geometry_selection()
    app.processEvents()
    assert window._geometry_highlights == []
    assert all(item.scene() is None for item in overlays)
    window.scene_result = None
    window.close()


def test_no_candidate_without_rejected_geometry_still_gets_visible_zone() -> None:
    app = _app()
    window, built = _window_with_h3_like_scene()

    window._review_locate_annotation(_h3_row(include_rejected=False))
    app.processEvents()

    assert len(window._geometry_highlights) == 2
    marker, search_zone = window._geometry_highlights
    assert isinstance(marker, QGraphicsRectItem)
    assert isinstance(search_zone, QGraphicsRectItem)
    assert marker.scene() is built.scene
    assert search_zone.scene() is built.scene
    assert "не е одобрена" not in window.statusBar().currentMessage()

    window._cancel_manual_geometry_selection()
    window.scene_result = None
    window.close()


def test_long_rejected_path_does_not_expand_local_review_zoom() -> None:
    app = _app()
    window, built = _window_with_h3_like_scene(long_rejected_path=True)

    window._review_locate_annotation(_h3_row(include_rejected=True))
    app.processEvents()

    assert len(window._geometry_highlights) == 3
    search_zone = window._geometry_highlights[1]
    rejected_overlay = window._geometry_highlights[2]
    assert isinstance(search_zone, QGraphicsRectItem)
    assert isinstance(rejected_overlay, QGraphicsPathItem)
    assert rejected_overlay.path().boundingRect().width() > search_zone.rect().width() * 10

    visible_rect = window.drawing_view.mapToScene(
        window.drawing_view.viewport().rect()
    ).boundingRect()
    zone_rect = search_zone.rect()
    assert visible_rect.left() <= zone_rect.left()
    assert visible_rect.right() >= zone_rect.right()
    assert visible_rect.top() <= zone_rect.top()
    assert visible_rect.bottom() >= zone_rect.bottom()
    assert visible_rect.width() / zone_rect.width() < 3.0
    assert visible_rect.height() / zone_rect.height() < 3.0

    window._cancel_manual_geometry_selection()
    window.scene_result = None
    window.close()


class _ReviewCenterStub(QWidget):
    def set_geometry_selection_active(
        self,
        _active: bool,
        _annotation_text: str = "",
    ) -> None:
        return None

    def place_away_from(self, _global_point) -> None:
        return None


def test_direct_review_dock_close_leaves_manual_mode_cleanly() -> None:
    app = _app()
    window, built = _window_with_h3_like_scene()
    window._schedule_ai_diagnostic_bundle = lambda: None

    dock = QDockWidget("Review", window)
    dock.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    dialog = _ReviewCenterStub(dock)
    dock.setWidget(dialog)
    window._review_center = dialog
    window._review_dock = dock
    dock.destroyed.connect(
        lambda _obj=None, current_dock=dock, current=dialog: window._review_dock_destroyed(
            current_dock,
            current,
        )
    )
    window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
    dock.show()

    window._review_locate_annotation(_h3_row(include_rejected=True))
    app.processEvents()
    overlays = tuple(window._geometry_highlights)
    assert window._manual_selection_row is not None
    assert window._manual_selection_scene is built.scene
    assert window.drawing_view._selection_entity_filter is not None
    assert window._manual_selection_toolbar.isVisible()
    assert overlays

    # Exercise the user's direct X/close path, not MainWindow._close_review_center().
    dock.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()

    assert window._review_center is None
    assert window._review_dock is None
    assert window._manual_selection_row is None
    assert window._manual_selection_scene is None
    assert window.drawing_view._selection_entity_filter is None
    assert not window._manual_selection_toolbar.isVisible()
    assert window._geometry_highlights == []
    assert all(item.scene() is None for item in overlays)

    window.scene_result = None
    window.close()
