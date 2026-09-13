from __future__ import annotations

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsSimpleTextItem,
)

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import EnginePackage
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
    layer: str = "ARM_SHAIBI",
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type=kind,
        layer=layer,
        geometry=geometry,
        text=text,
    )


def _window_with_reference_geometry_scene() -> tuple[MainWindow, object]:
    annotation = _record(
        "9843B1::BJ-FI-L",
        "BLOCK_ATTR_TEXT",
        {"insert": [3830.57, 5137.64, 0.0], "height": 13.5},
        text="56N8x150",
        layer="ARM_SHAIBI_TXT",
    )
    fragment_a = _record(
        "PXG_9843B1_0_0",
        "LINE",
        {"start": [3800.0, 5100.0, 0.0], "end": [3860.0, 5100.0, 0.0]},
    )
    fragment_b = _record(
        "PXG_9843B1_1_0",
        "LINE",
        {"start": [3860.0, 5100.0, 0.0], "end": [3860.0, 5160.0, 0.0]},
    )
    # A remote object makes a whole-sheet fit visibly different from the
    # intended annotation-local review view (mirrors test_rc42's pattern).
    remote = _record(
        "REMOTE",
        "LINE",
        {"start": [9000.0, 9000.0, 0.0], "end": [9200.0, 9000.0, 0.0]},
    )
    built = build_scene([annotation, fragment_a, fragment_b, remote])
    window = MainWindow(
        _RepositoryStub(),
        logging.getLogger("reference-geometry-preview"),
        session_service=_SessionStub(),
    )
    window.scene_result = built
    window.drawing_view.setScene(built.scene)
    window.resize(1200, 800)
    window.show()
    _app().processEvents()
    return window, built


def _no_candidate_row(*, reference_handles: list[str]) -> dict:
    return {
        "annotation_handle": "9843B1::BJ-FI-L",
        "annotation_text": "56N8x150",
        "declared_length_cm": 150.0,
        "candidate_handles": [],
        "reference_geometry_handles": list(reference_handles),
        # Matches the real "56N8x150" case verified earlier against the
        # production DxfImporter/batch_geometry_report path.
        "suggested_shape_code": "F006",
        "suggested_segments_cm": [46.0, 21.0, 8.0],
        "matcher_blocker_codes": ["NO_CANDIDATE", "NO_MASTER_MATCH"],
        "review_reason_codes": [],
        "recognition_policy": {
            "min_search_distance_raw": 55.0,
            "max_search_distance_raw": 90.0,
            "search_distance_ratio": 0.25,
            "extended_search_distance_raw": 240.0,
            "extended_search_distance_ratio": 0.60,
        },
    }


def test_reference_geometry_preview_draws_blue_highlight_not_red() -> None:
    app = _app()
    window, built = _window_with_reference_geometry_scene()
    row = _no_candidate_row(
        reference_handles=["PXG_9843B1_0_0", "PXG_9843B1_1_0"]
    )

    window._review_preview_reference_geometry(row)
    app.processEvents()

    assert window._geometry_highlights, "expected blue reference overlays to be drawn"
    path_items = [
        item for item in window._geometry_highlights if isinstance(item, QGraphicsPathItem)
    ]
    frame_items = [
        item for item in window._geometry_highlights if isinstance(item, QGraphicsRectItem)
    ]
    label_items = [
        item
        for item in window._geometry_highlights
        if isinstance(item, QGraphicsSimpleTextItem)
    ]
    assert len(path_items) == 2
    assert len(frame_items) == 1
    assert len(label_items) == 1

    for item in path_items:
        assert item.pen().color().name() == "#0369a1"
    assert frame_items[0].pen().color().name() == "#0369a1"

    from PySide6.QtCore import Qt as _Qt

    assert frame_items[0].pen().style() == _Qt.PenStyle.DashLine

    # Never the candidate-preview red used for PROVEN candidates.
    assert all(item.pen().color().name() != "#e00000" for item in path_items + frame_items)

    assert built.handle_to_item["PXG_9843B1_0_0"].isSelected()
    assert built.handle_to_item["PXG_9843B1_1_0"].isSelected()

    status_message = window.statusBar().currentMessage()
    assert "СПРАВОЧНА" in status_message
    assert "НЕ е потвърден кандидат" in status_message

    # Safety boundary: the reference-only preview must never touch
    # candidate_handles or promote the row toward auto-approval eligibility.
    assert row["candidate_handles"] == []

    window.scene_result = None
    window.close()


def test_reference_geometry_preview_falls_back_when_handles_not_in_scene() -> None:
    app = _app()
    window, built = _window_with_reference_geometry_scene()
    row = _no_candidate_row(reference_handles=["DOES_NOT_EXIST_1", "DOES_NOT_EXIST_2"])

    window._review_preview_reference_geometry(row)
    app.processEvents()

    # No usable centreline geometry -> falls back to the existing
    # annotation-locate + manual-selection path, same as a true no-candidate row.
    assert window._manual_selection_row is not None
    assert window._manual_selection_row.get("annotation_handle") == "9843B1::BJ-FI-L"

    window._cancel_manual_geometry_selection()
    window.scene_result = None
    window.close()


def test_reference_geometry_preview_falls_back_when_handles_list_empty() -> None:
    app = _app()
    window, built = _window_with_reference_geometry_scene()
    row = _no_candidate_row(reference_handles=[])

    window._review_preview_reference_geometry(row)
    app.processEvents()

    assert window._manual_selection_row is not None

    window._cancel_manual_geometry_selection()
    window.scene_result = None
    window.close()
