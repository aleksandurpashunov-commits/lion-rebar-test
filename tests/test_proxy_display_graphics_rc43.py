from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import ezdxf
import pytest
from ezdxf import colors as ezcolors
from ezdxf.entities import ACADProxyEntity, factory

from lion_rebar.dxf.geometry_core_v2 import (
    arc_path_from_geometry,
    polyline_path_from_geometry,
)
from lion_rebar.dxf.importer import DxfImporter
from lion_rebar.dxf.proxy_graphics import (
    PROXY_DISPLAY_SOURCE,
    extract_proxy_display_graphics,
)


def _add_proxy(document, layout, *, layer: str = "0", color: int = 256):
    proxy = factory.create_db_entry(
        "ACAD_PROXY_ENTITY",
        {"layer": layer, "color": color},
        document,
    )
    proxy.proxy_graphic = b"synthetic-proxy-graphic"
    layout.add_entity(proxy)
    return proxy


def _virtual(document, kind: str, **dxfattribs):
    return factory.new(kind, dxfattribs=dxfattribs, doc=document)


def _install_proxy_payloads(monkeypatch, payloads: dict[str, Any]) -> None:
    def virtual_entities(self):
        payload = payloads[str(self.dxf.handle)]
        if isinstance(payload, BaseException):
            raise payload
        values = payload(self) if callable(payload) else payload
        for entity in values:
            entity.set_source_of_copy(self)
            yield entity

    monkeypatch.setattr(ACADProxyEntity, "virtual_entities", virtual_entities)


def _record_factory():
    return DxfImporter()._record_from_entity


def _assert_points_approx(actual, expected) -> None:
    assert len(actual) == len(expected)
    for actual_point, expected_point in zip(actual, expected):
        assert actual_point == pytest.approx(expected_point)


def test_extracts_supported_graphics_on_any_layer_with_exact_curve_and_text_data(
    monkeypatch,
) -> None:
    document = ezdxf.new("R2010")
    document.layers.add("NOT_GA", color=7)
    proxy = _add_proxy(document, document.modelspace(), layer="NOT_GA", color=3)

    polyline = _virtual(document, "LWPOLYLINE", layer="0", color=256)
    polyline.set_points(
        [(0.0, 0.0, 0.5), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0)],
        format="xyb",
    )
    polyline.closed = True
    arc = _virtual(
        document,
        "ARC",
        layer="0",
        color=21,
        center=(4.0, 5.0, 0.0),
        radius=3.0,
        start_angle=15.0,
        end_angle=125.0,
    )
    circle = _virtual(
        document,
        "CIRCLE",
        layer="0",
        color=4,
        center=(20.0, 5.0, 0.0),
        radius=2.0,
    )
    text = _virtual(
        document,
        "TEXT",
        layer="0",
        color=0,
        text="Proxy label",
        insert=(30.0, 7.0, 0.0),
        height=2.5,
        rotation=37.0,
    )
    ellipse = _virtual(
        document,
        "ELLIPSE",
        layer="0",
        color=256,
        true_color=ezcolors.rgb2int((250, 250, 250)),
        center=(40.0, 5.0, 0.0),
        major_axis=(4.0, 0.0, 0.0),
        ratio=0.5,
        start_param=0.25,
        end_param=math.pi,
    )
    spline = _virtual(document, "SPLINE", layer="0", color=5)
    spline.set_open_uniform(
        [(50.0, 0.0, 0.0), (55.0, 10.0, 0.0), (60.0, 0.0, 0.0)],
        degree=2,
    )
    _install_proxy_payloads(
        monkeypatch,
        {str(proxy.dxf.handle): [polyline, arc, circle, text, ellipse, spline]},
    )

    first = extract_proxy_display_graphics(
        document,
        list(document.modelspace()),
        record_factory=_record_factory(),
    )
    second = extract_proxy_display_graphics(
        document,
        list(document.modelspace()),
        record_factory=_record_factory(),
    )

    assert first.proxy_instance_count == 1
    assert first.discovered_primitive_count == 6
    assert first.rendered_primitive_count == 6
    assert first.complete_proxy_count == 1
    assert first.partial_proxy_count == 0
    assert first.type_counts == {
        "ARC": 1,
        "CIRCLE": 1,
        "ELLIPSE": 1,
        "LWPOLYLINE": 1,
        "SPLINE": 1,
        "TEXT": 1,
    }
    assert [record.handle for record in first.records] == [
        record.handle for record in second.records
    ]
    assert all(record.layer == "NOT_GA" for record in first.records)
    assert all(record.raw["source"] == PROXY_DISPLAY_SOURCE for record in first.records)
    assert all(record.raw["display_only"] is True for record in first.records)
    assert all(record.raw["engineering_eligible"] is False for record in first.records)
    assert all(record.raw["display_id"] == record.handle for record in first.records)
    assert all(
        record.raw["proxy_source_key"] == record.raw["source_key"]
        for record in first.records
    )
    assert all(
        record.raw["parent_proxy_handle"] == str(proxy.dxf.handle)
        for record in first.records
    )
    assert all(record.raw["origin_proxy_verified"] for record in first.records)

    records = {record.entity_type: record for record in first.records}
    assert records["LWPOLYLINE"].geometry["closed"] is True
    assert records["LWPOLYLINE"].geometry["bulges"][0] == pytest.approx(0.5)
    assert records["ARC"].geometry["center"] == pytest.approx([4.0, 5.0, 0.0])
    assert records["ARC"].geometry["radius"] == pytest.approx(3.0)
    assert records["ARC"].geometry["start_angle"] == pytest.approx(15.0)
    assert records["ARC"].geometry["end_angle"] == pytest.approx(125.0)
    assert records["ARC"].geometry["display_only"] is True
    assert records["ARC"].geometry["source_key"] == records["ARC"].raw["source_key"]
    assert records["TEXT"].text == "Proxy label"
    assert records["TEXT"].geometry["insert"] == pytest.approx([30.0, 7.0, 0.0])
    assert records["TEXT"].geometry["height"] == pytest.approx(2.5)
    assert records["TEXT"].geometry["rotation"] == pytest.approx(37.0)
    assert records["ELLIPSE"].geometry["ratio"] == pytest.approx(0.5)
    assert records["ELLIPSE"].raw["source_rgb"] == "#FAFAFA"
    assert records["ELLIPSE"].raw["display_rgb"] != "#FAFAFA"
    assert records["SPLINE"].geometry["degree"] == 2
    assert len(records["SPLINE"].geometry["control_points"]) == 3
    assert len(records["SPLINE"].geometry["render_points"]) > 3

    suppressed_key = first.records[0].raw["source_key"]
    suppressed = extract_proxy_display_graphics(
        document,
        list(document.modelspace()),
        record_factory=_record_factory(),
        suppressed_source_keys=frozenset({suppressed_key}),
    )
    assert suppressed.rendered_primitive_count == 5
    assert suppressed.suppressed_duplicate_count == 1
    assert suppressed.unsupported_primitive_count == 0
    assert suppressed.complete_proxy_count == 1
    assert suppressed.diagnostics[0]["suppressed_duplicate_count"] == 1


def test_nested_minsert_applies_transform_and_keeps_instance_identity(monkeypatch) -> None:
    document = ezdxf.new("R2010")
    document.layers.add("VISIBLE_PROXY", color=4)
    inner = document.blocks.new("INNER_PROXY")
    proxy = _add_proxy(document, inner, layer="0", color=0)
    outer = document.blocks.new("OUTER_PROXY")
    outer.add_blockref(
        "INNER_PROXY",
        (5.0, 0.0, 0.0),
        dxfattribs={"layer": "0", "rotation": 90.0},
    )
    top = document.modelspace().add_blockref(
        "OUTER_PROXY",
        (100.0, 200.0, 0.0),
        dxfattribs={"layer": "VISIBLE_PROXY", "xscale": 2.0, "yscale": 2.0},
    )
    top.grid(size=(1, 2), spacing=(0.0, 50.0))

    line = _virtual(
        document,
        "LINE",
        layer="0",
        color=256,
        start=(0.0, 0.0, 0.0),
        end=(10.0, 0.0, 0.0),
    )
    _install_proxy_payloads(monkeypatch, {str(proxy.dxf.handle): [line]})

    result = extract_proxy_display_graphics(
        document,
        list(document.modelspace()),
        record_factory=_record_factory(),
    )

    assert result.proxy_instance_count == 2
    assert result.discovered_primitive_count == 2
    assert result.rendered_primitive_count == 2
    assert result.complete_proxy_count == 2
    assert len({record.handle for record in result.records}) == 2
    assert len({record.raw["source_key"] for record in result.records}) == 2
    assert all(record.layer == "VISIBLE_PROXY" for record in result.records)
    assert all(len(record.raw["insertion_path"]) == 2 for record in result.records)
    assert {
        record.raw["insertion_path"][0]["occurrence_index"]
        for record in result.records
    } == {0, 1}
    first = result.records[0]
    assert first.geometry["start"] == pytest.approx([110.0, 200.0, 0.0])
    assert first.geometry["end"] == pytest.approx([110.0, 220.0, 0.0])
    assert first.raw["origin_proxy_verified"] is True
    assert first.raw["proxy_instance_id"] == first.raw["instance_key"]


def test_reflected_insert_and_negative_ocs_are_serialized_in_world_coordinates(
    monkeypatch,
) -> None:
    document = ezdxf.new("R2010")
    block = document.blocks.new("MIRRORED_PROXY")
    proxy = _add_proxy(document, block)
    x_insert = document.modelspace().add_blockref(
        "MIRRORED_PROXY",
        (100.0, 0.0, 0.0),
        dxfattribs={"xscale": -1.0},
    )
    y_insert = document.modelspace().add_blockref(
        "MIRRORED_PROXY",
        (0.0, 100.0, 0.0),
        dxfattribs={"yscale": -1.0},
    )

    lwpolyline = _virtual(document, "LWPOLYLINE", layer="0")
    lwpolyline.set_points([(1.0, 2.0, 0.5), (5.0, 2.0, 0.0)], format="xyb")
    polyline = _virtual(document, "POLYLINE", layer="0")
    polyline.append_vertex((1.0, 7.0, 0.0), {"bulge": 0.5})
    polyline.append_vertex((5.0, 7.0, 0.0), {"bulge": 0.0})
    arc = _virtual(
        document,
        "ARC",
        layer="0",
        center=(3.0, 4.0, 0.0),
        radius=2.0,
        start_angle=10.0,
        end_angle=100.0,
    )
    text = _virtual(
        document,
        "TEXT",
        layer="0",
        text="Mirror",
        insert=(2.0, 3.0, 0.0),
        height=2.0,
        rotation=30.0,
    )
    ellipse = _virtual(
        document,
        "ELLIPSE",
        layer="0",
        center=(8.0, 4.0, 0.0),
        major_axis=(2.0, 0.0, 0.0),
        ratio=0.5,
        start_param=0.2,
        end_param=1.5,
    )

    direct_proxy = _add_proxy(document, document.modelspace())
    direct_lwpolyline = _virtual(
        document,
        "LWPOLYLINE",
        layer="0",
        extrusion=(0.0, 0.0, -1.0),
    )
    direct_lwpolyline.set_points(
        [(1.0, 2.0, 0.5), (5.0, 2.0, 0.0)], format="xyb"
    )
    direct_arc = _virtual(
        document,
        "ARC",
        layer="0",
        center=(3.0, 4.0, 0.0),
        radius=2.0,
        start_angle=10.0,
        end_angle=100.0,
        extrusion=(0.0, 0.0, -1.0),
    )
    direct_text = _virtual(
        document,
        "TEXT",
        layer="0",
        text="OCS",
        insert=(2.0, 3.0, 0.0),
        height=2.0,
        rotation=30.0,
        extrusion=(0.0, 0.0, -1.0),
    )
    _install_proxy_payloads(
        monkeypatch,
        {
            str(proxy.dxf.handle): [lwpolyline, polyline, arc, text, ellipse],
            str(direct_proxy.dxf.handle): [
                direct_lwpolyline,
                direct_arc,
                direct_text,
            ],
        },
    )

    result = extract_proxy_display_graphics(
        document,
        list(document.modelspace()),
        record_factory=_record_factory(),
    )

    assert result.proxy_instance_count == 3
    assert result.rendered_primitive_count == 13
    by_insert: dict[str, dict[str, Any]] = {}
    direct: dict[str, Any] = {}
    for record in result.records:
        source_kind = record.raw["source_primitive_type"]
        path = record.raw["insertion_path"]
        if not path:
            direct[source_kind] = record
        else:
            by_insert.setdefault(path[0]["insert_handle"], {})[source_kind] = record

    x_records = by_insert[str(x_insert.dxf.handle)]
    x_lw = x_records["LWPOLYLINE"]
    assert x_lw.raw["transformed_extrusion"] == pytest.approx([0.0, 0.0, -1.0])
    _assert_points_approx(
        x_lw.geometry["points"], [[99.0, 2.0, -0.5], [95.0, 2.0, 0.0]]
    )
    _assert_points_approx(
        x_lw.geometry["wcs_points"], [[99.0, 2.0, 0.0], [95.0, 2.0, 0.0]]
    )
    x_lw_path = polyline_path_from_geometry(x_lw.geometry)
    assert x_lw_path is not None
    assert x_lw_path.elements[0].sweep_deg == pytest.approx(
        -4.0 * math.degrees(math.atan(0.5))
    )

    x_poly = x_records["POLYLINE"]
    _assert_points_approx(
        x_poly.geometry["points"], [[99.0, 7.0, 0.0], [95.0, 7.0, 0.0]]
    )
    assert x_poly.geometry["bulges"] == pytest.approx([-0.5, 0.0])

    x_arc = x_records["ARC"]
    assert x_arc.geometry["center"] == pytest.approx([97.0, 4.0, 0.0])
    assert x_arc.geometry["start_angle"] == pytest.approx(80.0)
    assert x_arc.geometry["end_angle"] == pytest.approx(170.0)
    x_arc_path = arc_path_from_geometry(x_arc.geometry)
    assert x_arc_path is not None
    assert x_arc_path.elements[0].sweep_deg == pytest.approx(90.0)

    assert x_records["TEXT"].geometry["insert"] == pytest.approx(
        [98.0, 3.0, 0.0]
    )
    assert x_records["TEXT"].geometry["rotation"] == pytest.approx(150.0)
    assert x_records["TEXT"].geometry["height"] == pytest.approx(2.0)
    assert x_records["ELLIPSE"].geometry["center"] == pytest.approx(
        [92.0, 4.0, 0.0]
    )
    assert x_records["ELLIPSE"].geometry["major_axis"] == pytest.approx(
        [-2.0, 0.0, 0.0]
    )
    assert x_records["ELLIPSE"].geometry["minor_axis"] == pytest.approx(
        [0.0, 1.0, 0.0]
    )

    y_records = by_insert[str(y_insert.dxf.handle)]
    _assert_points_approx(
        y_records["LWPOLYLINE"].geometry["points"],
        [[1.0, 98.0, -0.5], [5.0, 98.0, 0.0]],
    )
    assert y_records["ARC"].geometry["center"] == pytest.approx(
        [3.0, 96.0, 0.0]
    )
    assert y_records["ARC"].geometry["start_angle"] == pytest.approx(260.0)
    assert y_records["ARC"].geometry["end_angle"] == pytest.approx(350.0)
    assert y_records["TEXT"].geometry["insert"] == pytest.approx(
        [2.0, 97.0, 0.0]
    )
    assert y_records["TEXT"].geometry["rotation"] == pytest.approx(-30.0)
    assert y_records["ELLIPSE"].geometry["minor_axis"] == pytest.approx(
        [0.0, -1.0, 0.0]
    )

    _assert_points_approx(
        direct["LWPOLYLINE"].geometry["points"],
        [[-1.0, 2.0, -0.5], [-5.0, 2.0, 0.0]],
    )
    assert direct["ARC"].geometry["center"] == pytest.approx([-3.0, 4.0, 0.0])
    assert direct["ARC"].geometry["start_angle"] == pytest.approx(80.0)
    assert direct["ARC"].geometry["end_angle"] == pytest.approx(170.0)
    assert direct["TEXT"].geometry["insert"] == pytest.approx([-2.0, 3.0, 0.0])
    assert direct["TEXT"].geometry["rotation"] == pytest.approx(150.0)


def test_nested_proxy_records_all_layers_that_control_visibility(monkeypatch) -> None:
    document = ezdxf.new("R2010")
    for name in ("OUTER_LAYER", "INNER_LAYER", "PROXY_LAYER", "PRIMITIVE_LAYER"):
        document.layers.add(name)
    inner = document.blocks.new("VISIBILITY_INNER")
    proxy = _add_proxy(document, inner, layer="PROXY_LAYER")
    outer = document.blocks.new("VISIBILITY_OUTER")
    outer.add_blockref(
        "VISIBILITY_INNER",
        (0.0, 0.0, 0.0),
        dxfattribs={"layer": "INNER_LAYER"},
    )
    document.modelspace().add_blockref(
        "VISIBILITY_OUTER",
        (10.0, 20.0, 0.0),
        dxfattribs={"layer": "OUTER_LAYER"},
    )
    line = _virtual(
        document,
        "LINE",
        layer="PRIMITIVE_LAYER",
        start=(0.0, 0.0, 0.0),
        end=(1.0, 0.0, 0.0),
    )
    _install_proxy_payloads(monkeypatch, {str(proxy.dxf.handle): [line]})

    result = extract_proxy_display_graphics(
        document,
        list(document.modelspace()),
        record_factory=_record_factory(),
    )

    assert result.rendered_primitive_count == 1
    record = result.records[0]
    assert record.layer == "PRIMITIVE_LAYER"
    assert [part["insert_layer"] for part in record.raw["insertion_path"]] == [
        "OUTER_LAYER",
        "INNER_LAYER",
    ]
    assert record.raw["visibility_layers"] == [
        "OUTER_LAYER",
        "INNER_LAYER",
        "PROXY_LAYER",
        "PRIMITIVE_LAYER",
    ]


def test_tilted_ocs_bulge_is_projected_as_curves_not_as_false_xy_circle(
    monkeypatch,
) -> None:
    document = ezdxf.new("R2010")
    proxy = _add_proxy(document, document.modelspace())
    normal = math.sqrt(0.5)
    polyline = _virtual(
        document,
        "LWPOLYLINE",
        layer="0",
        extrusion=(0.0, normal, normal),
    )
    polyline.set_points([(0.0, 0.0, 0.5), (4.0, 0.0, 0.0)], format="xyb")
    _install_proxy_payloads(monkeypatch, {str(proxy.dxf.handle): [polyline]})

    result = extract_proxy_display_graphics(
        document,
        list(document.modelspace()),
        record_factory=_record_factory(),
    )

    assert result.complete_proxy_count == 1
    assert result.rendered_primitive_count == 1
    record = result.records[0]
    assert record.raw["source_primitive_type"] == "LWPOLYLINE"
    assert record.entity_type == "SPLINE"
    assert record.geometry["coordinate_space"] == "WCS"
    assert record.geometry["curve_representation"] == "projected_cubic_bezier"
    assert len(record.geometry["bezier_segments"]) == 2
    assert any(
        abs(float(segment["control1"][1])) > 1e-6
        or abs(float(segment["control2"][1])) > 1e-6
        for segment in record.geometry["bezier_segments"]
    )
    assert len(record.geometry["render_points"]) > 4


def test_missing_corrupt_and_unsupported_proxy_graphics_are_isolated(monkeypatch) -> None:
    document = ezdxf.new("R2010")
    missing = _add_proxy(document, document.modelspace(), layer="0")
    missing.proxy_graphic = b""
    corrupt = _add_proxy(document, document.modelspace(), layer="0")
    partial = _add_proxy(document, document.modelspace(), layer="0")

    line = _virtual(
        document,
        "LINE",
        layer="0",
        start=(0.0, 0.0, 0.0),
        end=(1.0, 0.0, 0.0),
    )
    unsupported_hatch = _virtual(document, "HATCH", layer="0")
    _install_proxy_payloads(
        monkeypatch,
        {
            str(corrupt.dxf.handle): ValueError("damaged proxy bytes"),
            str(partial.dxf.handle): [line, unsupported_hatch],
        },
    )

    result = extract_proxy_display_graphics(
        document,
        list(document.modelspace()),
        record_factory=_record_factory(),
    )

    assert result.proxy_instance_count == 3
    assert result.discovered_primitive_count == 2
    assert result.rendered_primitive_count == 1
    assert result.unsupported_primitive_count == 1
    assert result.missing_graphic_count == 1
    assert result.unreadable_proxy_count == 1
    assert result.partial_proxy_count == 1
    assert result.status_counts == {"missing": 1, "partial": 1, "unreadable": 1}
    assert result.unsupported_type_counts == {"HATCH": 1}
    assert result.records[0].entity_type == "LINE"

    by_handle = {
        entry["source_proxy_handle"]: entry
        for entry in result.diagnostics
        if entry["kind"] == "proxy"
    }
    assert by_handle[str(missing.dxf.handle)]["status"] == "missing"
    assert by_handle[str(corrupt.dxf.handle)]["status"] == "unreadable"
    assert "damaged proxy bytes" in by_handle[str(corrupt.dxf.handle)]["reason"]
    assert by_handle[str(partial.dxf.handle)]["status"] == "partial"
    assert "HATCH=1" in by_handle[str(partial.dxf.handle)]["reason"]


def test_cycle_guard_keeps_graphics_before_cycle_and_reports_traversal(monkeypatch) -> None:
    document = ezdxf.new("R2010")
    cyclic = document.blocks.new("CYCLIC_PROXY")
    proxy = _add_proxy(document, cyclic, layer="0")
    cyclic.add_blockref("CYCLIC_PROXY", (20.0, 0.0, 0.0))
    document.modelspace().add_blockref("CYCLIC_PROXY", (5.0, 7.0, 0.0))
    line = _virtual(
        document,
        "LINE",
        layer="0",
        start=(0.0, 0.0, 0.0),
        end=(3.0, 0.0, 0.0),
    )
    _install_proxy_payloads(monkeypatch, {str(proxy.dxf.handle): [line]})

    result = extract_proxy_display_graphics(
        document,
        list(document.modelspace()),
        record_factory=_record_factory(),
    )

    assert result.proxy_instance_count == 1
    assert result.rendered_primitive_count == 1
    assert result.traversal_diagnostic_count == 1
    assert result.unreadable_proxy_count == 0
    assert result.status_counts == {"complete": 1, "unreadable": 1}
    traversal = [entry for entry in result.diagnostics if entry["kind"] == "block_traversal"]
    assert len(traversal) == 1
    assert traversal[0]["reason_code"] == "CYCLIC_BLOCK_REFERENCE"
    assert len(traversal[0]["insertion_path"]) == 2
    assert result.records[0].geometry["start"] == pytest.approx([5.0, 7.0, 0.0])


_REAL_DXF = Path(
    os.environ.get(
        "LION_REBAR_PROXY_DISPLAY_TEST_DXF",
        r"C:\Users\Александър\OneDrive\Работен плот\DXF проекти\39_83_shaibi_+21.48+24.78  do +24.78+28.08.dxf",
    )
)


@pytest.mark.skipif(not _REAL_DXF.is_file(), reason="confirmed proxy DXF is unavailable")
def test_confirmed_shaibi_dxf_recovers_all_available_supported_proxy_graphics() -> None:
    document = ezdxf.readfile(_REAL_DXF)
    result = extract_proxy_display_graphics(
        document,
        list(document.modelspace()),
        record_factory=_record_factory(),
    )

    assert result.proxy_instance_count == 341
    assert result.discovered_primitive_count == 3803
    assert result.rendered_primitive_count == 3803
    assert result.suppressed_duplicate_count == 0
    assert result.unsupported_primitive_count == 0
    assert result.complete_proxy_count == 341
    assert result.partial_proxy_count == 0
    assert result.missing_graphic_count == 0
    assert result.unreadable_proxy_count == 0
    assert result.type_counts == {
        "ARC": 439,
        "CIRCLE": 708,
        "POLYLINE": 1171,
        "TEXT": 1485,
    }
    assert sum(record.layer == "arm_shaibi" for record in result.records) > 0
    assert sum(record.entity_type == "TEXT" for record in result.records) == 1485
    assert sum(
        record.entity_type == "TEXT"
        and abs(float(record.geometry.get("rotation", 0.0))) > 1e-9
        for record in result.records
    ) == 334
