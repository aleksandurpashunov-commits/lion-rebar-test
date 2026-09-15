from pathlib import Path

from lion_rebar.engine.bootstrap import create_bootstrap_engine
from lion_rebar.engine.package_io import export_engine_bundle, inspect_engine_bundle


def test_engine_bundle_roundtrip(tmp_path: Path):
    engine = create_bootstrap_engine()
    bundle = export_engine_bundle(engine, tmp_path / "bootstrap.lionengine")
    loaded, manifest, names = inspect_engine_bundle(bundle)
    assert loaded.engine_id == engine.engine_id
    assert manifest["engine_version"] == engine.version
    assert "engine.json" in names


def test_v0316_vid_profiles_and_shape_specific_text() -> None:
    from lion_rebar.export.master_engine_v031 import render_master_svg
    from lion_rebar.export.shape_image import _vid_render_profile

    # Generic open shapes are reduced slightly inside the unchanged PNG canvas.
    assert _vid_render_profile("F003") == (0.94, 26)
    assert _vid_render_profile("F004") == (0.94, 26)
    assert _vid_render_profile("F009") == (0.94, 26)

    # F006/F007 use the full cell so their production dimensions stay
    # readable; F008 keeps its established footprint.
    assert _vid_render_profile("F006") == (1.0, 18)
    assert _vid_render_profile("F007") == (1.0, 18)
    assert _vid_render_profile("F008") == (1.0, 18)

    stirrup_svg = render_master_svg("F006", {"L1": 10, "L2": 45, "L3": 25})
    c_shape_svg = render_master_svg("F007", {"L1": 10, "L2": 20})
    chair_svg = render_master_svg("F008", {"L1": 15, "L2": 20, "L3": 35})
    generic_svg = render_master_svg("F004", {"L1": 225, "L2": 15})

    assert "font-size:6px" in stirrup_svg
    assert "font-size:6px" in c_shape_svg
    assert "font-size:5.6px" in chair_svg
    assert "font-size:3.5px" in chair_svg  # rear-leg label
    assert "font-size:4.6px" in generic_svg


def test_v0316_schematic_hooks_standard_f006_and_production_total() -> None:
    import re

    from lion_rebar.export.master_engine_v031 import parameters_from_segments, render_master_svg

    # F001 always displays the reviewed total, not the close DXF measurement.
    straight = parameters_from_segments("F001", (269.9,), production_length_cm=270.0)
    assert straight.values == {"L1": 270.0}
    assert ">270<" in render_master_svg("F001", straight.values)
    assert ">269.9<" not in render_master_svg("F001", straight.values)

    # Short hooks are schematically enlarged while the real labels are kept.
    f004 = render_master_svg("F004", {"L1": 225, "L2": 15})
    match = re.search(r'<polyline class="rebar-line" points="([^"]+)"', f004)
    assert match
    points = [tuple(map(float, item.split(","))) for item in match.group(1).split()]
    hook_visual = abs(points[2][1] - points[1][1])
    main_visual = abs(points[1][0] - points[0][0])
    assert hook_visual / main_visual >= 0.24
    assert ">225<" in f004 and ">15<" in f004

    f003 = render_master_svg("F003", {"L1": 20, "L2": 175, "L3": 20})
    match = re.search(r'<polyline class="rebar-line" points="([^"]+)"', f003)
    assert match
    points = [tuple(map(float, item.split(","))) for item in match.group(1).split()]
    left_hook = abs(points[1][1] - points[0][1])
    right_hook = abs(points[3][1] - points[2][1])
    main_visual = abs(points[2][0] - points[1][0])
    assert left_hook / main_visual >= 0.24
    assert right_hook / main_visual >= 0.24
    assert f003.count(">20<") == 2 and ">175<" in f003

    # Different real F006 dimensions use exactly the same visual contour/hooks.
    standard = render_master_svg("F006", {"L1": 10, "L2": 45, "L3": 25})
    other = render_master_svg("F006", {"L1": 12, "L2": 90, "L3": 20})
    shape_pattern = re.compile(r'(<rect class="rebar-line"[^>]+/>|<line class="rebar-line"[^>]+/>)')
    assert shape_pattern.findall(standard) == shape_pattern.findall(other)
    assert 'hook-inside-label' in standard
    assert standard.count('class="dimension-line"') == 2
    assert ">45<" in standard and ">25<" in standard and ">10<" in standard
    assert ">90<" in other and ">20<" in other and ">12<" in other


def test_v01441_adaptive_f003_orientation_uses_production_proportions() -> None:
    import re

    from lion_rebar.export.master_engine_v031 import render_master_svg

    def points(svg: str) -> list[tuple[float, float]]:
        match = re.search(r'<polyline class="rebar-line" points="([^"]+)"', svg)
        assert match
        return [tuple(map(float, item.split(","))) for item in match.group(1).split()]

    short_arms_up = points(
        render_master_svg("F003", {"L1": 15, "L2": 300, "L3": 20})
    )
    assert short_arms_up[0][0] == short_arms_up[1][0]
    assert short_arms_up[1][1] == short_arms_up[2][1]
    assert short_arms_up[2][0] == short_arms_up[3][0]

    open_right = points(
        render_master_svg("F003", {"L1": 80, "L2": 20, "L3": 70})
    )
    assert open_right[0][1] == open_right[1][1]
    assert open_right[1][0] == open_right[2][0]
    assert open_right[2][1] == open_right[3][1]


def test_v01441_f010_is_supported_and_rendered_open_right() -> None:
    import re

    from lion_rebar.export.master_engine_v031 import (
        parameters_from_segments,
        render_master_svg,
    )

    params = parameters_from_segments(
        "F010",
        (200, 28, 19, 10, 113),
        production_length_cm=370,
    )
    assert params.total_length_cm == 370
    svg = render_master_svg("F010", params.values)
    for value in (">200<", ">28<", ">19<", ">10<", ">113<"):
        assert value in svg
    match = re.search(r'<polyline class="rebar-line" points="([^"]+)"', svg)
    assert match
    points = [tuple(map(float, item.split(","))) for item in match.group(1).split()]
    assert len(points) == 6
    assert points[1][0] > points[0][0]
    assert points[5][0] > points[4][0]
