from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.excel_export import ExportPosition
from lion_rebar.ui.drawing_view import build_scene
from lion_rebar.ui.review_center import ReviewCenterDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _position(
    code: str,
    segments: tuple[float, ...],
    turns: tuple[float, ...],
    *,
    handle: str,
    length: float,
) -> ExportPosition:
    return ExportPosition(
        annotation_handle=handle,
        annotation_text=f"1N8x{length:g}",
        count=1,
        diameter_mm=8.0,
        production_length_cm=length,
        shape_code=code,
        shape_name=code,
        shape_revision=1,
        geometry_handles=(),
        frame_index=1,
        segment_lengths_cm=segments,
        turn_angles_deg=turns,
    )


def test_resolved_dimension_uses_cad_text_and_cad_position() -> None:
    _app()
    record = DxfEntityRecord(
        handle="DB81",
        entity_type="DIMENSION",
        layer="koti",
        text="21",
        geometry={
            "measurement": 50.0,
            "display_text": "21",
            "defpoint2": [0.0, 0.0, 0.0],
            "defpoint3": [50.0, 0.0, 0.0],
            "text_midpoint": [999.0, 999.0, 0.0],
            "render_primitives": [
                {"kind": "LINE", "start": [10.0, 10.0, 0.0], "end": [30.0, 10.0, 0.0]},
                {"kind": "TEXT", "text": "21", "insert": [20.0, 12.0, 0.0], "height": 2.5, "rotation": 0.0},
            ],
        },
    )

    built = build_scene([record])
    item = built.handle_to_item["DB81"]
    labels = [child for child in item.childItems() if isinstance(child, QGraphicsSimpleTextItem)]

    assert [label.text() for label in labels] == ["21"]
    assert labels[0].pos().x() == pytest.approx(20.0)
    assert labels[0].pos().y() == pytest.approx(-12.0)
    assert "50" not in {label.text() for label in labels}


@pytest.mark.skipif(importlib.util.find_spec("ezdxf") is None, reason="ezdxf is not installed")
def test_importer_preserves_explicit_dimension_and_resolved_primitives(tmp_path: Path) -> None:
    import ezdxf

    from lion_rebar.dxf.importer import DxfImporter

    document = ezdxf.new("R2010")
    dimension = document.modelspace().add_linear_dim(
        base=(1.5, 2.0), p1=(0.0, 0.0), p2=(3.0, 0.0)
    )
    dimension.dimension.dxf.text = "21"
    dimension.render()
    path = tmp_path / "dimension.dxf"
    document.saveas(path)

    result = DxfImporter().import_file(path)
    record = next(entity for entity in result.entities if entity.entity_type == "DIMENSION")

    assert record.text == "21"
    assert record.geometry["display_text"] == "21"
    assert any(item["kind"] == "TEXT" and item["text"] == "21" for item in record.geometry["render_primitives"])
    assert any(item["kind"] == "LINE" for item in record.geometry["render_primitives"])


def test_f005_missing_fixed_turns_does_not_split_total_but_f006_geometry_does() -> None:
    f005_old = _position("F005", (40.0, 14.0, 40.0), (), handle="A", length=94.0)
    f005_full = _position("F005", (40.0, 14.0, 40.0), (90.0, -90.0), handle="B", length=94.0)
    f006_narrow = _position("F006", (10.0, 20.0, 165.0, 20.0, 165.0, 10.0), (), handle="C", length=395.0)
    f006_wide = _position("F006", (10.0, 25.0, 160.0, 25.0, 165.0, 10.0), (), handle="D", length=395.0)

    assert f005_old.group_key == f005_full.group_key
    assert f006_narrow.group_key != f006_wide.group_key


def test_embedded_review_actions_remain_pinned_in_compact_height(tmp_path: Path) -> None:
    _app()
    report = tmp_path / "report.json"
    state = tmp_path / "state.json"
    report.write_text(
        '{"schema":"lion-rebar-geometry-batch-report/v3","project_name":"P","generated_at":"2026-09-05T10:00:00+03:00","summary":{},"rows":[]}',
        encoding="utf-8",
    )
    dialog = ReviewCenterDialog(report, state_path=state, embedded=True)
    dialog.resize(460, 600)
    dialog.show()
    QApplication.processEvents()

    assert dialog.scroll_area.widgetResizable()
    assert dialog.scroll_area.horizontalScrollBarPolicy().name == "ScrollBarAlwaysOff"
    assert dialog.approve_button.isVisible()
    top = dialog.approve_button.mapTo(dialog, QPoint(0, 0)).y()
    assert 0 <= top < dialog.height()
    assert top + dialog.approve_button.height() <= dialog.height()
    dialog.close()
