from __future__ import annotations

from pathlib import Path

import pytest

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.dxf_position_export import (
    POSITION_LAYER,
    _block_name,
    _choose_marker_center,
    _marker_style,
    _number_text_height,
    generate_dxf_position_export,
)
from lion_rebar.export.excel_export import ExportPosition
from lion_rebar.project.models import ProjectModel


def test_marker_is_readable_but_compact() -> None:
    record = DxfEntityRecord(
        "T1",
        "TEXT",
        "ARM_TEXT",
        {
            "height": 2.5,
            "insert": [100.0, 50.0, 0.0],
            "visual_bbox": [[100.0, 48.75, 0.0], [118.0, 51.25, 0.0]],
        },
        text="15N12x200",
    )
    style = _marker_style([record])
    assert style.radius == pytest.approx(4.5)
    assert _number_text_height(style.radius, 74) == pytest.approx(style.radius * 0.92)
    assert _number_text_height(style.radius, 154) < _number_text_height(style.radius, 74)

    center = _choose_marker_center(record, style, [])
    assert center[0] < 100.0
    assert 48.75 <= center[1] <= 51.25


def test_dense_markers_are_not_placed_on_top_of_each_other() -> None:
    record = DxfEntityRecord(
        "T1",
        "TEXT",
        "ARM_TEXT",
        {
            "height": 2.5,
            "insert": [100.0, 50.0, 0.0],
            "visual_bbox": [[100.0, 48.75, 0.0], [118.0, 51.25, 0.0]],
        },
        text="15N12x200",
    )
    style = _marker_style([record])
    first = _choose_marker_center(record, style, [])
    second = _choose_marker_center(record, style, [first])
    assert first != second


def test_dxf_export_creates_circle_text_blocks(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "original.dxf"
    doc = ezdxf.new("R2010")
    doc.modelspace().add_text(
        "15N12x200",
        height=2.5,
        dxfattribs={"insert": (100.0, 50.0), "layer": "ARM_TEXT"},
    )
    doc.saveas(source)

    project = ProjectModel(
        name="DXF BLOCK TEST",
        root=str(tmp_path),
        source_dxf="source/original.dxf",
    )
    entities = [
        DxfEntityRecord(
            "T1",
            "TEXT",
            "ARM_TEXT",
            {
                "height": 2.5,
                "insert": [100.0, 50.0, 0.0],
                "visual_bbox": [[100.0, 48.75, 0.0], [118.0, 51.25, 0.0]],
            },
            text="15N12x200",
        )
    ]
    positions = [
        ExportPosition(
            "T1",
            "15N12x200",
            15,
            12,
            200,
            "F001",
            "ПРАВ ПРЪТ",
            1,
            ("G1",),
            1,
            (200,),
            (),
        )
    ]
    output = tmp_path / "output" / "test_ПОЗИЦИИ.dxf"
    result = generate_dxf_position_export(
        project,
        entities,
        positions,
        {"T1": 74},
        output,
    )

    assert result.marker_count == 1
    assert result.unique_position_count == 1
    assert result.layer_name == POSITION_LAYER
    assert result.traceability_path.is_file()

    exported = ezdxf.readfile(result.dxf_path)
    block_name = _block_name(74)
    block = exported.blocks.get(block_name)
    entity_types = [entity.dxftype() for entity in block]
    assert entity_types.count("CIRCLE") == 1
    assert entity_types.count("TEXT") == 1
    texts = [entity.dxf.text for entity in block if entity.dxftype() == "TEXT"]
    assert texts == ["74"]

    refs = [
        entity
        for entity in exported.modelspace()
        if entity.dxftype() == "INSERT" and entity.dxf.name == block_name
    ]
    assert len(refs) == 1
    assert refs[0].dxf.layer == POSITION_LAYER
    assert float(refs[0].dxf.rotation) == 0.0
