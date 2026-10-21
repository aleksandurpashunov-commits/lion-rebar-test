from __future__ import annotations

import html
import os
import sys
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.export.engine_shape_parameters import parameters_for_shape
from lion_rebar.export.engine_draft_render import render_shape_draft_svg
from lion_rebar.export.master_engine_v031 import (
    MasterDimensionError,
    master_engine_preflight,
    parameters_from_segments,
    render_master_svg,
)

CANVAS_WIDTH = 900
CANVAS_HEIGHT = 360

# Keep an application created for a headless renderer alive for the entire
# process. Qt GUI objects (notably SVG text/font rendering) are not reliable
# before a QGuiApplication/QApplication exists on Windows.
_HEADLESS_QT_APP = None
_SVG_FONT_FAMILY: str | None = None


@dataclass(frozen=True)
class _SvgTextLabel:
    """One text node extracted from a generated Engine SVG.

    QtSvg in the managed Windows runtime paints the line work correctly but
    either drops SVG text completely or replaces glyphs with square dots.
    Keeping the text metadata separate lets QPainter rasterize the exact same
    labels with the installed system font after QtSvg has drawn the geometry.
    """

    text: str
    x: float
    y: float
    text_anchor: str
    dominant_baseline: str
    rotation_deg: float
    font_family: str
    font_size_px: float
    font_weight: int
    italic: bool
    fill: str


@dataclass(frozen=True)
class _RenderedSvgTextLabel:
    text: str
    bounds: tuple[float, float, float, float]


def _ensure_qt_gui_application():
    """Return the active QApplication, creating an offscreen one if needed.

    The desktop application already owns a QApplication. Managed release tests
    execute the rasterizer directly, before the desktop UI starts. On Windows,
    QSvgRenderer can block while resolving text/fonts if no GUI application
    exists. Creating one explicitly makes the same renderer deterministic in
    both paths.
    """

    global _HEADLESS_QT_APP

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    current = QCoreApplication.instance()
    if current is not None:
        if isinstance(current, QApplication):
            return current
        raise RuntimeError(
            "MASTER SVG rasterization requires QApplication; a non-GUI "
            "QCoreApplication is already active."
        )

    # This branch is used by release tests and command-line export checks. The
    # actual desktop application never reaches it because its QApplication is
    # created in lion_rebar.app before the main window is constructed.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _HEADLESS_QT_APP = QApplication(["lion-rebar-master-renderer"])
    return _HEADLESS_QT_APP


def _ensure_svg_text_font() -> str:
    """Register a real Windows font when the offscreen Qt DB is empty."""

    global _SVG_FONT_FAMILY

    from PySide6.QtGui import QFontDatabase

    families = tuple(QFontDatabase.families())
    if _SVG_FONT_FAMILY and _SVG_FONT_FAMILY in families:
        return _SVG_FONT_FAMILY

    by_lower = {family.lower(): family for family in families}
    for preferred in ("arial", "segoe ui", "tahoma"):
        if preferred in by_lower:
            _SVG_FONT_FAMILY = by_lower[preferred]
            return _SVG_FONT_FAMILY

    windows_root = Path(os.environ.get("WINDIR") or "C:/Windows")
    candidates = (
        windows_root / "Fonts" / "arial.ttf",
        windows_root / "Fonts" / "arialbd.ttf",
        windows_root / "Fonts" / "segoeui.ttf",
        windows_root / "Fonts" / "tahoma.ttf",
    )
    registered: list[str] = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(candidate))
        if font_id < 0:
            continue
        registered.extend(QFontDatabase.applicationFontFamilies(font_id))

    families = tuple(QFontDatabase.families())
    by_lower = {family.lower(): family for family in families}
    for preferred in ("arial", "segoe ui", "tahoma"):
        if preferred in by_lower:
            _SVG_FONT_FAMILY = by_lower[preferred]
            return _SVG_FONT_FAMILY
    if registered:
        _SVG_FONT_FAMILY = registered[0]
        return _SVG_FONT_FAMILY
    if families:
        _SVG_FONT_FAMILY = families[0]
        return _SVG_FONT_FAMILY
    raise MasterDimensionError(
        "MASTER размерните надписи не могат да се изчертаят: "
        "Qt няма регистриран четим Windows шрифт."
    )


def render_shape_png(
    path: Path,
    *,
    shape: ShapeDefinition,
    segment_lengths_cm: Sequence[float],
    turn_angles_deg: Sequence[float] = (),
    production_length_cm: float | None = None,
) -> Path:
    """Render the Excel VID image through the locked MASTER v0.3.1 renderer.

    This function deliberately contains no independent shape geometry. The
    F-code, orientation, dimension placement and F006/F009 special rendering
    come from the exact approved OPEN_ENGINE v0.3.1 source shipped with the
    release. Only reviewed production dimensions are substituted.
    """

    master_engine_preflight()
    code = str(shape.code or "").strip().upper()
    # Продуктово решение (собственик, 28.09.2026): стандартният режим
    # възпроизвежда ДОКАЗАНАТА ФОРМА ОТ ПРОЕКТА. Заключеният MASTER рисувач
    # чертае шаблона на кода и подадените завои не влияят на геометрията му —
    # затова, когато позицията носи завои, РАЗЛИЧНИ от шаблонните, рисунката
    # се прави от самите завои. MASTER остава за преизползване: кодът, името и
    # всичко, което проектът не доказва.
    if _turns_contradict_shape(shape, turn_angles_deg):
        from lion_rebar.export.proven_form import начертай

        return _rasterize_svg(
            начертай(list(segment_lengths_cm), list(turn_angles_deg)),
            path, shape_code=code,
        )
    if code in LOCKED_MASTER_DRAWN_CODES:
        parameters = parameters_from_segments(
            code,
            _canonical_drawing_order(code, segment_lengths_cm),
            production_length_cm=production_length_cm,
            turn_angles_deg=turn_angles_deg,
        )
        svg = render_master_svg(code, parameters.values, include_safe_zone=False)
    else:
        # От F010 нагоре рисува ОБЩИЯТ рисувач на редактора, пренесен в
        # ``engine_draft_render``. Собственик, 23.09.2026: „много добре ги
        # рисува и много лесно“ — и преди това, за стълбищните рамена: „имаш
        # мастър форма и тя не е такава, намери я в Енджина“.
        #
        # Границата не е избрана тук: заключеният HTML описва сам F001..F008
        # в ``geometry()`` плюс отделните рисунки на F006 и F009. Всичко над
        # тях е чернова и в редактора минава през общия рисувач.
        parameters = parameters_for_shape(
            shape,
            segment_lengths_cm,
            production_length_cm=production_length_cm,
            turn_angles_deg=turn_angles_deg,
        )
        svg = render_shape_draft_svg(shape, parameters.expanded_segments_cm)
    return _rasterize_svg(svg, path, shape_code=code)


#: Формите със СОБСТВЕНА рисунка в заключения MASTER: F001..F008 от
#: ``geometry()``, плюс F006 и F009 с отделните си рисувачи.
LOCKED_MASTER_DRAWN_CODES = frozenset(f"F{index:03d}" for index in range(1, 10))

#: Под колко градуса разликата между доказания и шаблонния завой се смята за
#: същия ъгъл. По-малко би пращало през другия рисувач всяко закръгляне.
_TURN_MATCH_TOLERANCE_DEG = 5.0


def shape_turn_angles_deg(shape: ShapeDefinition) -> tuple[float, ...]:
    """Завоите на самата форма, със знак: положителен = наляво.

    Четат се от дефиницията, не от таблица в кода — същият източник, от който
    рисува и редакторът.
    """

    изход: list[float] = []
    for сегмент in sorted(shape.segments, key=lambda с: int(с.index)):
        ъгъл = getattr(сегмент, "bend_after_deg", None)
        if ъгъл is None:
            continue
        посока = str(getattr(сегмент, "bend_direction", "") or "").strip().upper()
        изход.append(float(ъгъл) if посока.startswith("L") else -float(ъгъл))
    return tuple(изход)


def _turns_contradict_shape(
    shape: ShapeDefinition,
    turn_angles_deg: Sequence[float],
) -> bool:
    """Противоречат ли подадените завои на шаблона на формата.

    Празни или несъвпадащи по брой завои значат „няма какво да се сравнява" —
    тогава рисува шаблонът, както досега.
    """

    подадени = [
        float(с) for с in (turn_angles_deg or ())
        if isinstance(с, (int, float))
    ]
    if len(подадени) < 1:
        return False
    шаблонни = shape_turn_angles_deg(shape)
    if len(шаблонни) != len(подадени):
        return False
    return any(
        abs(abs(а) - abs(б)) > _TURN_MATCH_TOLERANCE_DEG
        for а, б in zip(подадени, шаблонни)
    )

def _canonical_drawing_order(code: str, lengths: Sequence[float]) -> tuple[float, ...]:
    """Редът, в който заключената форма иска да бъде НАРИСУВАНА.

    ЗАЩО. Собственик, 23.09.2026, за „3N14x555“ [10, 545]: „завърти я наляво
    един път да е хоризонтална“. Обхождането на DXF беше дало късото рамо
    първо, а ``_generic_svg`` рисува L1 водоравно — значи прътът излизаше
    отвесен, 10 cm широк и 545 висок.

    Самата ENGINE дефиниция на F004 казва обратното: „Основният сегмент стои
    хоризонтално отдолу; винкелът се изчертава нагоре в десния край.“ Тук се
    спазва тя. Същото правило вече стои и в
    ``excel_export._canonicalize_for_master`` за другия път.

    Пипа се САМО редът за рисуване. Производствените стойности не се менят —
    те са същите две числа и се записват другаде.
    """

    values = tuple(float(value) for value in lengths)
    if str(code).strip().upper() == "F004" and len(values) == 2:
        основен = 0 if values[0] >= values[1] else 1
        return (values[основен], values[1 - основен])
    return values


def _visible_alpha_bounds(image, qrect_type):
    """Return the bounding rectangle of non-transparent pixels.

    Do not use QBitmap/QRegion here. On some Windows PySide6 builds those
    GUI-backed conversions can block indefinitely. QImage is a pure raster
    buffer, so scanning its alpha channel is deterministic.
    """

    width = image.width()
    height = image.height()
    if width <= 0 or height <= 0:
        return qrect_type()

    bytes_per_line = image.bytesPerLine()
    raw = memoryview(image.constBits())
    alpha_offset = 3 if sys.byteorder == "little" else 0

    min_x = width
    min_y = height
    max_x = -1
    max_y = -1

    for y in range(height):
        start = y * bytes_per_line + alpha_offset
        alpha = bytes(raw[start : start + width * 4 : 4])
        visible = alpha.rstrip(b"\x00")
        if not visible:
            continue
        row_min = len(alpha) - len(alpha.lstrip(b"\x00"))
        row_max = len(visible) - 1
        min_x = min(min_x, row_min)
        max_x = max(max_x, row_max)
        min_y = min(min_y, y)
        max_y = y

    if max_x < min_x or max_y < min_y:
        return qrect_type()
    return qrect_type(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def _vid_render_profile(shape_code: str) -> tuple[float, int]:
    """Return (content scale, alpha-crop padding) for the Excel VID image.

    The Excel cell and PNG canvas are fixed. Generic open shapes are given a
    little more breathing room, while the approved F006 stirrup keeps its
    established footprint. F008 keeps its footprint so only its labels grow.
    """

    code = str(shape_code or "").strip().upper()
    if code in {"F006", "F007"}:
        return 1.0, 18
    if code == "F008":
        return 1.0, 18
    return 0.94, 26


_SVG_TEXT_RE = re.compile(r"<text\b([^>]*)>(.*?)</text>", flags=re.IGNORECASE | re.DOTALL)
_SVG_ATTRIBUTE_RE = re.compile(r"([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*\"([^\"]*)\"")
_SVG_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", flags=re.IGNORECASE | re.DOTALL)
_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _css_declarations(value: str) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for item in str(value or "").split(";"):
        if ":" not in item:
            continue
        key, raw = item.split(":", 1)
        key = key.strip().lower()
        if key:
            declarations[key] = raw.strip()
    return declarations


def _svg_number(value: str | None, default: float) -> float:
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", str(value or ""))
    if not match:
        return float(default)
    try:
        return float(match.group(0))
    except ValueError:
        return float(default)


def _extract_svg_text_labels(svg: str) -> tuple[str, tuple[_SvgTextLabel, ...]]:
    """Remove SVG text and return its exact paint metadata.

    The generated Engine SVGs use simple text nodes (no textPath/tspan).
    We intentionally remove them before QSvgRenderer sees the document, which
    also prevents the broken square-glyph fallback observed for F006.
    """

    class_styles: dict[str, dict[str, str]] = {}
    for style_match in _SVG_STYLE_RE.finditer(svg):
        for selector_text, declarations_text in _CSS_RULE_RE.findall(style_match.group(1)):
            declarations = _css_declarations(declarations_text)
            for selector in selector_text.split(","):
                selector = selector.strip()
                if selector.startswith(".") and len(selector) > 1:
                    class_styles.setdefault(selector[1:], {}).update(declarations)

    labels: list[_SvgTextLabel] = []

    def remove_text(match: re.Match[str]) -> str:
        attributes = {
            key.lower(): html.unescape(value)
            for key, value in _SVG_ATTRIBUTE_RE.findall(match.group(1))
        }
        style: dict[str, str] = {}
        for class_name in attributes.get("class", "").split():
            style.update(class_styles.get(class_name, {}))
        style.update(_css_declarations(attributes.get("style", "")))
        for key in (
            "fill",
            "font-family",
            "font-size",
            "font-weight",
            "font-style",
            "text-anchor",
            "dominant-baseline",
        ):
            if key in attributes:
                style[key] = attributes[key]

        raw_text = re.sub(r"<[^>]+>", "", match.group(2))
        text = html.unescape(raw_text).strip()
        if not text:
            return ""

        transform = attributes.get("transform", "")
        rotation_match = re.search(
            r"rotate\(\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))",
            transform,
            flags=re.IGNORECASE,
        )
        weight_value = str(style.get("font-weight", "400")).strip().lower()
        if weight_value in {"bold", "bolder"}:
            font_weight = 700
        elif weight_value == "lighter":
            font_weight = 300
        else:
            font_weight = int(round(_svg_number(weight_value, 400.0)))

        labels.append(
            _SvgTextLabel(
                text=text,
                x=_svg_number(attributes.get("x"), 0.0),
                y=_svg_number(attributes.get("y"), 0.0),
                text_anchor=str(style.get("text-anchor", "start")).strip().lower(),
                dominant_baseline=str(style.get("dominant-baseline", "auto")).strip().lower(),
                rotation_deg=float(rotation_match.group(1)) if rotation_match else 0.0,
                font_family=str(style.get("font-family", "Arial")).split(",", 1)[0].strip(" \"'") or "Arial",
                font_size_px=max(1.0, _svg_number(style.get("font-size"), 4.6)),
                font_weight=font_weight,
                italic=str(style.get("font-style", "normal")).strip().lower() == "italic",
                fill=str(style.get("fill", "#17365d")).strip() or "#17365d",
            )
        )
        return ""

    geometry_svg = _SVG_TEXT_RE.sub(remove_text, svg)
    return geometry_svg, tuple(labels)


def _paint_svg_text_labels(
    painter,
    labels: Sequence[_SvgTextLabel],
    view_box,
    target_rect,
) -> tuple[_RenderedSvgTextLabel, ...]:
    """Paint extracted SVG labels with QPainter and return audit bounds."""

    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QTransform

    if view_box.width() <= 0 or view_box.height() <= 0:
        raise MasterDimensionError("MASTER SVG няма валиден viewBox за размерните надписи.")

    scale = min(
        float(target_rect.width()) / float(view_box.width()),
        float(target_rect.height()) / float(view_box.height()),
    )
    offset_x = (
        float(target_rect.left())
        + (float(target_rect.width()) - float(view_box.width()) * scale) / 2.0
        - float(view_box.left()) * scale
    )
    offset_y = (
        float(target_rect.top())
        + (float(target_rect.height()) - float(view_box.height()) * scale) / 2.0
        - float(view_box.top()) * scale
    )

    available_families = {family.lower(): family for family in QFontDatabase.families()}
    fallback_family = _SVG_FONT_FAMILY or next(iter(available_families.values()), "Arial")
    rendered: list[_RenderedSvgTextLabel] = []
    for label in labels:
        anchor_x = offset_x + label.x * scale
        anchor_y = offset_y + label.y * scale

        font_family = available_families.get(label.font_family.lower(), fallback_family)
        font = QFont(font_family)
        font.setPixelSize(max(1, int(round(label.font_size_px * scale))))
        font.setBold(label.font_weight >= 600)
        font.setItalic(label.italic)
        metrics = QFontMetricsF(font)
        advance = float(metrics.horizontalAdvance(label.text))
        ink = metrics.boundingRect(label.text)

        if label.text_anchor == "middle":
            text_x = -advance / 2.0
        elif label.text_anchor in {"end", "right"}:
            text_x = -advance
        else:
            text_x = 0.0

        if label.dominant_baseline in {"central", "middle"}:
            text_y = -float(ink.center().y())
        elif label.dominant_baseline in {"hanging", "text-before-edge"}:
            text_y = float(metrics.ascent())
        else:
            text_y = 0.0

        color = QColor(label.fill)
        if not color.isValid():
            color = QColor("#17365d")

        painter.save()
        try:
            painter.setFont(font)
            painter.setPen(color)
            painter.translate(anchor_x, anchor_y)
            if abs(label.rotation_deg) > 1e-9:
                painter.rotate(label.rotation_deg)
            painter.drawText(QPointF(text_x, text_y), label.text)
        finally:
            painter.restore()

        local_bounds = ink.translated(text_x, text_y)
        transform = QTransform()
        transform.translate(anchor_x, anchor_y)
        if abs(label.rotation_deg) > 1e-9:
            transform.rotate(label.rotation_deg)
        device_bounds = transform.mapRect(local_bounds)
        rendered.append(
            _RenderedSvgTextLabel(
                text=label.text,
                bounds=(
                    float(device_bounds.x()),
                    float(device_bounds.y()),
                    float(device_bounds.width()),
                    float(device_bounds.height()),
                ),
            )
        )
    return tuple(rendered)


def _rasterize_svg(svg: str, path: Path, *, shape_code: str = "") -> Path:
    try:
        from PySide6.QtCore import QByteArray, QRect, QRectF, Qt
        from PySide6.QtGui import QColor, QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
    except ImportError as exc:
        raise RuntimeError(
            "PySide6.QtSvg е необходим за директно възпроизвеждане на MASTER формите."
        ) from exc

    # Must happen before creating QSvgRenderer/QPainter. This is the decisive
    # Windows managed-test fix; changing only the crop algorithm is too late.
    _ensure_qt_gui_application()
    _ensure_svg_text_font()

    path.parent.mkdir(parents=True, exist_ok=True)
    geometry_svg, text_labels = _extract_svg_text_labels(svg)
    renderer = QSvgRenderer(QByteArray(geometry_svg.encode("utf-8")))
    if not renderer.isValid():
        raise MasterDimensionError("MASTER renderer-ът генерира невалидно SVG.")
    renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)

    source_size = 900
    source = QImage(source_size, source_size, QImage.Format.Format_ARGB32)
    source.fill(Qt.GlobalColor.transparent)
    painter = QPainter(source)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    try:
        renderer.render(painter, QRectF(0, 0, source_size, source_size))
    finally:
        painter.end()

    if text_labels:
        painter = QPainter(source)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        try:
            _paint_svg_text_labels(
                painter,
                text_labels,
                renderer.viewBoxF(),
                QRectF(0, 0, source_size, source_size),
            )
        finally:
            painter.end()

    bounds = _visible_alpha_bounds(source, QRect)
    if bounds.isNull() or bounds.width() <= 0 or bounds.height() <= 0:
        raise MasterDimensionError("MASTER SVG не съдържа видима форма.")
    content_scale, pad = _vid_render_profile(shape_code)
    bounds.adjust(-pad, -pad, pad, pad)
    bounds = bounds.intersected(source.rect())
    cropped = source.copy(bounds)
    target_width = max(1, round((CANVAS_WIDTH - 36) * content_scale))
    target_height = max(1, round((CANVAS_HEIGHT - 28) * content_scale))
    scaled = cropped.scaled(
        target_width,
        target_height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    final = QImage(CANVAS_WIDTH, CANVAS_HEIGHT, QImage.Format.Format_ARGB32)
    final.fill(QColor("white"))
    painter = QPainter(final)
    try:
        x = (CANVAS_WIDTH - scaled.width()) // 2
        y = (CANVAS_HEIGHT - scaled.height()) // 2
        painter.drawImage(x, y, scaled)
    finally:
        painter.end()
    if not final.save(str(path), "PNG"):
        raise RuntimeError(f"Не може да се запише изображението на формата: {path}")
    return path
