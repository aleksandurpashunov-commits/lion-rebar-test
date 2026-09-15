from __future__ import annotations

import re
import math

import pytest

from lion_rebar.export.master_engine_v031 import render_master_svg
from lion_rebar.export.shape_image import _extract_svg_text_labels, _vid_render_profile


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _polyline_points(svg: str) -> tuple[tuple[float, float], ...]:
    match = re.search(r'<polyline class="rebar-line" points="([^"]+)"', svg)
    assert match
    return tuple(
        tuple(float(value) for value in point.split(","))
        for point in match.group(1).split()
    )


def _curve_endpoint(svg: str) -> tuple[float, float]:
    match = re.search(r'<path class="rebar-line" d="([^"]+)"', svg)
    assert match
    values = [float(value) for value in re.findall(_NUMBER, match.group(1))]
    assert len(values) == 8
    return values[-2], values[-1]


def _point_to_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    vx, vy = end[0] - start[0], end[1] - start[1]
    wx, wy = point[0] - start[0], point[1] - start[1]
    denominator = vx * vx + vy * vy
    if denominator <= 1e-12:
        return math.hypot(wx, wy)
    fraction = min(1.0, max(0.0, (wx * vx + wy * vy) / denominator))
    nearest = (start[0] + fraction * vx, start[1] + fraction * vy)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


@pytest.mark.parametrize(
    "values",
    (
        {"L1": 50.0, "L2": 25.0},
        {"L1": 10.0, "L2": 200.0},
        {"L1": 10.0, "L2": 20.0},
    ),
)
def test_f007_extreme_proportions_stay_a_clear_c_with_real_dimensions(
    values: dict[str, float],
) -> None:
    svg = render_master_svg("F007", values)
    points = _polyline_points(svg)
    assert len(points) == 3

    vertical_span = abs(points[1][1] - points[0][1])
    horizontal_span = abs(points[2][0] - points[1][0])
    assert 1.2 <= horizontal_span / vertical_span <= 4.0

    curve_end = _curve_endpoint(svg)
    assert curve_end[0] > points[1][0]

    _geometry_svg, labels = _extract_svg_text_labels(svg)
    expected_l1 = f"{int(values['L1'])}"
    expected_l2 = f"{int(values['L2'])}"
    assert tuple(label.text for label in labels) == (
        expected_l1,
        expected_l2,
        expected_l1,
    )
    assert all(label.font_size_px >= 6.0 for label in labels)


def test_f006_and_f007_dimensions_use_close_full_cell_profiles() -> None:
    stirrup = render_master_svg("F006", {"L1": 10.0, "L2": 45.0, "L3": 25.0})
    c_shape = render_master_svg("F007", {"L1": 10.0, "L2": 20.0})

    assert _vid_render_profile("F006") == (1.0, 18)
    assert _vid_render_profile("F007") == (1.0, 18)
    assert "font-size:6px" in stirrup
    assert "font-size:6px" in c_shape

    rect_y = float(re.search(r'<rect class="rebar-line"[^>]+ y="(' + _NUMBER + r')"', stirrup).group(1))
    top_dim_y = float(re.search(r'<line class="dimension-line"[^>]+ y1="(' + _NUMBER + r')"', stirrup).group(1))
    assert rect_y - top_dim_y == pytest.approx(5.5)

    # All F007 dimensions explicitly use the compact 5.5-unit offset.
    dimension_lines = re.findall(r'<line class="dimension-line"[^>]+>', c_shape)
    assert len(dimension_lines) == 3

    hook_label = re.search(
        r'<text class="dimension-text hook-inside-label" x="('
        + _NUMBER
        + r')" y="('
        + _NUMBER
        + r')"',
        stirrup,
    )
    assert hook_label
    anchor = (float(hook_label.group(1)), float(hook_label.group(2)))
    hook_segments = [
        tuple(float(value) for value in re.findall(_NUMBER, element))
        for element in re.findall(r'<line class="rebar-line"[^>]+>', stirrup)
    ]
    assert len(hook_segments) == 2
    assert all(
        _point_to_segment_distance(
            anchor,
            (segment[0], segment[1]),
            (segment[2], segment[3]),
        )
        >= 8.0
        for segment in hook_segments
    )
