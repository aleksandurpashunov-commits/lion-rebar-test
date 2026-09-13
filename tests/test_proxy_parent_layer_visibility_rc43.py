from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QApplication

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.ui.drawing_view import DrawingView, build_scene


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _FitRecordingView(DrawingView):
    def __init__(self) -> None:
        super().__init__()
        self.last_fit_rect: QRectF | None = None

    def fitInView(self, rect, mode=Qt.AspectRatioMode.IgnoreAspectRatio):  # noqa: N802
        self.last_fit_rect = QRectF(rect)
        return super().fitInView(rect, mode)


def test_nested_proxy_display_requires_content_and_insert_layers_visible() -> None:
    _app()
    display = DxfEntityRecord(
        handle="PXVIS::nested",
        entity_type="LINE",
        layer="PROXY_CONTENT",
        geometry={"start": [0.0, 0.0, 0.0], "end": [10.0, 0.0, 0.0]},
        raw={
            "display_only": True,
            "engineering_eligible": False,
            "display_id": "PXVIS::nested",
            "visibility_layers": [
                "OUTER_INSERT",
                "INNER_INSERT",
                "PROXY_CONTENT",
            ],
        },
    )

    result = build_scene([], [display])
    item = result.display_id_to_item["PXVIS::nested"]
    assert item in result.layer_groups["OUTER_INSERT"]
    assert item in result.layer_groups["INNER_INSERT"]
    assert item in result.layer_groups["PROXY_CONTENT"]
    assert item.isVisible()

    result.set_layer_visibility("OUTER_INSERT", False)
    assert not item.isVisible()
    result.set_layer_visibility("PROXY_CONTENT", True)
    assert not item.isVisible(), "A child layer must not override a hidden parent INSERT."

    result.set_layer_visibility("OUTER_INSERT", True)
    assert item.isVisible()
    result.set_layer_visibility("INNER_INSERT", False)
    assert not item.isVisible()
    result.set_layer_visibility("OUTER_INSERT", False)
    result.set_layer_visibility("INNER_INSERT", True)
    assert not item.isVisible()

    result.set_layer_visibility("OUTER_INSERT", True)
    assert item.isVisible()


def test_fit_all_excludes_proxy_hidden_by_parent_insert_layer() -> None:
    _app()
    engineering = DxfEntityRecord(
        handle="VISIBLE",
        entity_type="LINE",
        layer="VISIBLE_CONTENT",
        geometry={"start": [0.0, 0.0, 0.0], "end": [10.0, 0.0, 0.0]},
    )
    display = DxfEntityRecord(
        handle="PXVIS::FAR",
        entity_type="LINE",
        layer="PROXY_CONTENT",
        geometry={"start": [1000.0, 0.0, 0.0], "end": [1010.0, 0.0, 0.0]},
        raw={
            "display_only": True,
            "engineering_eligible": False,
            "display_id": "PXVIS::FAR",
            "visibility_layers": ["PARENT_INSERT", "PROXY_CONTENT"],
        },
    )
    result = build_scene([engineering], [display])
    view = _FitRecordingView()
    view.resize(400, 200)
    view.setScene(result.scene)

    result.set_layer_visibility("PARENT_INSERT", False)
    view.fit_all()
    hidden_parent_scale = view.transform().m11()
    assert view.last_fit_rect is not None
    assert view.last_fit_rect.right() < 100.0

    result.set_layer_visibility("PARENT_INSERT", True)
    view.fit_all()
    assert view.last_fit_rect is not None
    assert view.last_fit_rect.right() > 1000.0
    assert view.transform().m11() < hidden_parent_scale
    view.close()
