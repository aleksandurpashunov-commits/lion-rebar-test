import importlib.util
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("ezdxf") is None, reason="ezdxf is not installed in this environment")


def test_basic_dxf_import(tmp_path: Path):
    import ezdxf

    from lion_rebar.dxf.importer import DxfImporter

    document = ezdxf.new("R2010")
    modelspace = document.modelspace()
    modelspace.add_line((0, 0), (100, 0), dxfattribs={"layer": "REBAR"})
    modelspace.add_text("8N10x635", dxfattribs={"layer": "TEXT", "height": 2.5}).set_placement((0, 10))
    path = tmp_path / "test.dxf"
    document.saveas(path)

    result = DxfImporter().import_file(path)
    assert result.summary.total_entities == 2
    assert result.summary.potential_rebar_texts == 1
    assert any(entity.handle for entity in result.entities)


def test_strict_proxy_color_resolver_covers_truecolor_bylayer_and_byblock() -> None:
    import ezdxf
    from ezdxf import colors as ezcolors

    from lion_rebar.dxf.importer import (
        _proxy_color_metadata,
        _proxy_effective_display_layer,
    )

    document = ezdxf.new("R2010")
    document.layers.add("PARENT_COLOR", color=2)
    document.layers.add("VIRTUAL_COLOR", color=5)
    parent = document.modelspace().add_line(
        (0.0, 0.0),
        (1.0, 0.0),
        dxfattribs={"layer": "PARENT_COLOR", "color": 6},
    )

    assert _proxy_effective_display_layer(
        "0", parent_proxy=parent, fallback_layer="FALLBACK"
    ) == "PARENT_COLOR"
    assert _proxy_effective_display_layer(
        "VIRTUAL_COLOR", parent_proxy=parent, fallback_layer="FALLBACK"
    ) == "VIRTUAL_COLOR"

    bylayer = _proxy_color_metadata(
        document,
        color_index=256,
        true_color=None,
        parent_proxy=parent,
        layer="VIRTUAL_COLOR",
    )
    assert bylayer["color_resolution"] == "BYLAYER"
    assert bylayer["source_rgb"] == "#0000FF"
    assert bylayer["display_rgb"] == "#0000FF"

    byblock = _proxy_color_metadata(
        document,
        color_index=0,
        true_color=None,
        parent_proxy=parent,
        layer="VIRTUAL_COLOR",
    )
    assert byblock["color_resolution"] == "BYBLOCK"
    assert byblock["source_rgb"] == "#FF00FF"
    assert byblock["display_rgb"] == "#D600D6"
    assert byblock["color_adjusted_for_contrast"] is True

    direct_truecolor = ezcolors.rgb2int((18, 52, 86))
    truecolor = _proxy_color_metadata(
        document,
        color_index=6,
        true_color=direct_truecolor,
        parent_proxy=parent,
        layer="VIRTUAL_COLOR",
    )
    assert truecolor["color_resolution"] == "TRUECOLOR"
    assert truecolor["source_rgb"] == "#123456"
    assert truecolor["display_rgb"] == "#123456"


def test_r10_strict_proxy_colors_survive_generic_display_suppression() -> None:
    from lion_rebar.dxf.importer import DxfImporter
    from lion_rebar.dxf.proxy_adapter import (
        PROXY_ANNOTATION_SOURCE,
        PROXY_GEOMETRY_SOURCE,
    )

    source = (
        Path(__file__).resolve().parents[3]
        / "verification_projects"
        / "rc41_r10_replay"
        / "source"
        / "original.dxf"
    )
    if not source.exists():
        pytest.skip(f"r10 DXF fixture is not available: {source}")

    result = DxfImporter().import_file(source)
    geometry = [
        record
        for record in result.entities
        if record.raw.get("source") == PROXY_GEOMETRY_SOURCE
    ]
    annotations = [
        record
        for record in result.entities
        if record.raw.get("source") == PROXY_ANNOTATION_SOURCE
    ]

    # Existing approved engineering output is unchanged; only portable display
    # metadata is added before the generic twins are suppressed.
    assert len(geometry) == 198
    assert len(annotations) == 130
    assert Counter(record.color_index for record in geometry) == {6: 178, 5: 20}
    assert Counter(record.raw.get("source_rgb") for record in geometry) == {
        "#FF00FF": 178,
        "#0000FF": 20,
    }
    assert Counter(record.raw.get("display_rgb") for record in geometry) == {
        "#D600D6": 178,
        "#0000FF": 20,
    }
    assert {record.raw.get("color_resolution") for record in geometry} == {"ACI"}

    fragments = [
        fragment
        for record in annotations
        for fragment in record.geometry.get("render_fragments", [])
    ]
    assert len(fragments) == 1023
    assert {fragment.get("source_rgb") for fragment in fragments} == {"#FF9F7F"}
    assert {fragment.get("display_rgb") for fragment in fragments} == {"#D63600"}

    strict_source_keys = {
        record.raw["proxy_source_key"]
        for record in geometry
        if record.raw.get("proxy_source_key")
    }
    display_source_keys = {
        record.raw.get("proxy_source_key") for record in result.display_entities
    }
    assert strict_source_keys.isdisjoint(display_source_keys)
