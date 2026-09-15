from __future__ import annotations

import hashlib
from itertools import combinations

import pytest

from lion_rebar.export.master_engine_v031 import render_master_svg
import lion_rebar.export.shape_image as shape_image


@pytest.mark.parametrize(
    ("shape_code", "values", "expected_labels", "copy_count"),
    (
        (
            "F007",
            {"L1": 10.0, "L2": 20.0},
            ("10", "20", "10"),
            1,
        ),
        (
            "F002",
            {
                "L1": 259.627289,
                "DX2": 40.372879,
                "DY2": 4.036866,
                "L3": 110.000038,
            },
            # 40.57 е ДЪЛЖИНАТА на редукцията (hypot), не проекцията ѝ по
            # оста: MASTER формулата сумира точно нея, и точно нея реже
            # арматуристът. 4.04 е отместването настрани.
            ("259.63", "40.57", "110", "4.04"),
            3,
        ),
        (
            "F006",
            {"L1": 10.0, "L2": 45.0, "L3": 25.0},
            ("45", "25", "10"),
            1,
        ),
    ),
)
def test_master_png_rasterizer_paints_exact_readable_labels(
    tmp_path,
    monkeypatch,
    shape_code: str,
    values: dict[str, float],
    expected_labels: tuple[str, ...],
    copy_count: int,
) -> None:
    from PySide6.QtCore import QRect, QRectF, Qt
    from PySide6.QtGui import QFontDatabase, QImage, QPainter

    shape_image._ensure_qt_gui_application()
    selected_font = shape_image._ensure_svg_text_font()
    assert selected_font in QFontDatabase.families()

    svg = render_master_svg(shape_code, values)
    geometry_svg, labels = shape_image._extract_svg_text_labels(svg)
    assert tuple(label.text for label in labels) == expected_labels
    assert "<text" not in geometry_svg.lower()

    # OCR-independent proof that every extracted label becomes real alpha
    # pixels, not missing glyphs or a parameter that never reaches QPainter.
    text_only = QImage(900, 900, QImage.Format.Format_ARGB32)
    text_only.fill(Qt.GlobalColor.transparent)
    painter = QPainter(text_only)
    try:
        rendered = shape_image._paint_svg_text_labels(
            painter,
            labels,
            QRectF(-12, -10, 124, 120),
            QRectF(0, 0, 900, 900),
        )
    finally:
        painter.end()
    assert tuple(item.text for item in rendered) == expected_labels
    for item in rendered:
        region = QRectF(*item.bounds).adjusted(-2, -2, 2, 2).toAlignedRect()
        region = region.intersected(text_only.rect())
        assert not region.isNull(), item.text
        cropped = text_only.copy(region)
        assert not shape_image._visible_alpha_bounds(cropped, QRect).isNull(), item.text

    if shape_code == "F002":
        # The real position-40 values must stay pairwise disjoint. This guards
        # the former 4.04/L1/corner collision at the compact VID scale.
        rectangles = [QRectF(*item.bounds) for item in rendered]
        assert all(not left.intersects(right) for left, right in combinations(rectangles, 2))

    # Spy on the real SVG -> PNG entry point, then prove the three position-40
    # occurrences are byte-identical.
    captured: list[tuple[str, ...]] = []
    original_painter = shape_image._paint_svg_text_labels

    def audited_painter(*args, **kwargs):
        result = original_painter(*args, **kwargs)
        captured.append(tuple(item.text for item in result))
        return result

    monkeypatch.setattr(shape_image, "_paint_svg_text_labels", audited_painter)
    hashes: list[str] = []
    for index in range(copy_count):
        output = tmp_path / f"{shape_code}_{index}.png"
        shape_image._rasterize_svg(svg, output, shape_code=shape_code)
        hashes.append(hashlib.sha256(output.read_bytes()).hexdigest())

    assert captured == [expected_labels] * copy_count
    assert len(set(hashes)) == 1
