from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsTextItem,
    QGraphicsView,
)

from lion_rebar.dxf.geometry_core_v2 import (
    PathElementKind,
    arc_path_from_geometry,
    polyline_path_from_geometry,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.text_normalization import decode_dxf_text


ENTITY_HANDLE_ROLE = 0
ENTITY_LAYER_ROLE = 1
ENTITY_TYPE_ROLE = 2
DISPLAY_SOURCE_ID_ROLE = 3
DISPLAY_ONLY_ROLE = 4
VISIBILITY_LAYERS_ROLE = 5

_VIEW_BACKGROUND = QColor("#f7f8fa")
_DEFAULT_GEOMETRY_COLOR = QColor("#202938")
_DEFAULT_TEXT_COLOR = QColor("#27364d")
_MINIMUM_DISPLAY_CONTRAST = 3.0

MANUAL_CENTERLINE_ENTITY_TYPES = frozenset(
    {"LINE", "LWPOLYLINE", "POLYLINE", "ARC"}
)

_HIT_TEST_PRIORITY = {
    "LINE": 0,
    "LWPOLYLINE": 0,
    "POLYLINE": 0,
    "ARC": 0,
    # Keep ordinary annotation picking unchanged outside manual geometry mode.
    # That mode applies an explicit centreline-only filter below.
    "BLOCK_ATTR_TEXT": 0,
    "TEXT": 0,
    "MTEXT": 0,
    "CIRCLE": 1,
    "LEADER": 2,
    "POINT": 3,
    "INSERT": 3,
    "DIMENSION": 9,
}


@dataclass
class SceneBuildResult:
    scene: QGraphicsScene
    layer_groups: dict[str, list[QGraphicsItem]]
    handle_to_item: dict[str, QGraphicsItem]
    handle_to_record: dict[str, DxfEntityRecord]
    display_id_to_item: dict[str, QGraphicsItem] = field(default_factory=dict)
    layer_visibility: dict[str, bool] = field(default_factory=dict)

    def set_layer_visibility(self, layer: str, visible: bool) -> None:
        """Apply DXF visibility through every layer controlling an item.

        A nested block primitive belongs to its effective content layer, but
        it is also hidden when any containing INSERT layer is hidden.  Keeping
        the state here makes the result independent of checkbox signal order:
        turning a child layer back on cannot reveal an item whose parent
        insertion layer is still off.
        """

        layer_name = str(layer or "0")
        self.layer_visibility[layer_name] = bool(visible)
        for item in self.layer_groups.get(layer_name, ()):
            controlling = item.data(VISIBILITY_LAYERS_ROLE)
            if not isinstance(controlling, (list, tuple)):
                controlling = (str(item.data(ENTITY_LAYER_ROLE) or layer_name),)
            item.setVisible(
                all(
                    self.layer_visibility.get(str(value or "0"), True)
                    for value in controlling
                )
            )


class DrawingView(QGraphicsView):
    mouseScenePositionChanged = Signal(float, float)
    #: Двата ъгъла на операторска рамка, в координати на чертежа.
    #: Издава се веднъж и режимът се самоизключва -- рамката е
    #: еднократно действие, не режим, в който операторът остава.
    operatorBoxDrawn = Signal(float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setBackgroundBrush(QBrush(_VIEW_BACKGROUND))
        self._panning = False
        self._pan_start = None
        self._box_preview: QGraphicsRectItem | None = None
        self._selection_entity_filter: frozenset[str] | None = None
        self._selection_allowed_handles: frozenset[str] | None = None
        self._box_mode = False
        self._box_origin = None

    def set_selection_entity_filter(
        self,
        entity_types: Iterable[str] | None,
        allowed_handles: Iterable[str] | None = None,
    ) -> None:
        """Limit selectable DXF entities without hiding the drawing.

        Existing callers can continue to pass only ``entity_types``.  Manual
        engineering assignment additionally supplies the authoritative rebar
        handles for the current import.  Passing ``None`` as ``entity_types``
        is the reset operation and restores both filters.
        """

        if entity_types is None:
            self._selection_entity_filter = None
            self._selection_allowed_handles = None
        else:
            self._selection_entity_filter = frozenset(
                str(value).upper() for value in entity_types
            )
            self._selection_allowed_handles = (
                frozenset(
                    str(value).strip().upper()
                    for value in allowed_handles
                    if str(value).strip()
                )
                if allowed_handles is not None
                else None
            )
        scene = self.scene()
        if scene is None:
            return
        for item in scene.items():
            if not item.data(ENTITY_HANDLE_ROLE):
                continue
            entity_type = str(item.data(ENTITY_TYPE_ROLE) or "").upper()
            handle = str(item.data(ENTITY_HANDLE_ROLE) or "").strip().upper()
            selectable = (
                self._selection_entity_filter is None
                or entity_type in self._selection_entity_filter
            ) and (
                self._selection_allowed_handles is None
                or handle in self._selection_allowed_handles
            )
            item.setFlag(QGraphicsItem.ItemIsSelectable, selectable)
            if not selectable and item.isSelected():
                item.setSelected(False)

    def wheelEvent(self, event):  # noqa: N802 - Qt API
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        current = self.transform().m11()
        next_scale = current * factor
        if 0.002 <= next_scale <= 500:
            self.scale(factor, factor)
        event.accept()

    def begin_operator_box(self) -> None:
        """Следващите два леви клика чертаят операторска рамка.

        Рамката е еднократно действие: след втория клик режимът се
        самоизключва, за да не остане операторът в него по невнимание.
        Десен бутон отказва започнатата рамка.
        """

        self._box_mode = True
        self._box_origin = None
        self._clear_box_preview()
        self.setCursor(Qt.CrossCursor)

    def cancel_operator_box(self) -> None:
        self._box_mode = False
        self._box_origin = None
        self._clear_box_preview()
        self.unsetCursor()

    def _clear_box_preview(self) -> None:
        item = getattr(self, "_box_preview", None)
        if item is not None:
            scene = item.scene()
            if scene is not None:
                scene.removeItem(item)
        self._box_preview = None

    def _update_box_preview(self, scene_point: QPointF) -> None:
        """Пунктираният правоъгълник между първия клик и показалеца.

        Без него операторът чертае на сляпо: вижда резултата чак когато
        рамката бъде приета или отказана. Прегледът е в СЦЕНИЧНИ координати
        (обърнат Y), защото се рисува на сцената -- за разлика от това, което
        сигналът излъчва навън.
        """

        origin = self._box_origin
        scene = self.scene()
        if origin is None or scene is None:
            return
        rect = QRectF(
            QPointF(origin[0], -origin[1]), scene_point
        ).normalized()
        if self._box_preview is None:
            pen = QPen(QColor("#1e88e5"))
            pen.setStyle(Qt.DashLine)
            pen.setCosmetic(True)
            pen.setWidth(2)
            item = QGraphicsRectItem(rect)
            item.setPen(pen)
            item.setBrush(QBrush(QColor(30, 136, 229, 28)))
            item.setZValue(10_000.0)
            scene.addItem(item)
            self._box_preview = item
        else:
            self._box_preview.setRect(rect)

    def _drawing_point(self, event) -> tuple[float, float]:
        """Клик -> координати на ЧЕРТЕЖА, не на сцената.

        Сцената е изглед отгоре с обърнат Y: всеки обект се поставя на
        ``-y`` (виж ``_build_text_item`` и рисуването на линиите), за да
        сочи екранното "надолу" натам, накъдето сочи и на хартия. Затова
        всяко място, което връща координати навън, обръща Y обратно --
        точно както ``mouseMoveEvent`` го прави за показалеца.

        Без това обръщане операторската рамка сравняваше екранни Y с
        чертожните Y на заглавията и никога не намираше заглавие вътре:
        в ``s-9`` заглавията стоят на y≈+2678, а рамката ги търсеше около
        y≈-2678.
        """

        scene_pos = self.mapToScene(event.position().toPoint())
        return (scene_pos.x(), -scene_pos.y())

    def mousePressEvent(self, event):  # noqa: N802 - Qt API
        if self._box_mode and event.button() == Qt.LeftButton:
            point = self._drawing_point(event)
            if self._box_origin is None:
                self._box_origin = point
            else:
                start = self._box_origin
                self.cancel_operator_box()
                self.operatorBoxDrawn.emit(
                    start[0], start[1], point[0], point[1]
                )
            event.accept()
            return
        if self._box_mode and event.button() == Qt.RightButton:
            self.cancel_operator_box()
            event.accept()
            return

        if event.button() in (Qt.MiddleButton, Qt.RightButton):
            self._panning = True
            self._pan_start = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            item = self._find_selectable_item_near(event.position().toPoint())
            if item is not None and self.scene() is not None:
                if event.modifiers() & Qt.ControlModifier:
                    item.setSelected(not item.isSelected())
                else:
                    self.scene().clearSelection()
                    item.setSelected(True)
                event.accept()
                return

        super().mousePressEvent(event)

    def _find_selectable_item_near(self, view_point: QPoint) -> QGraphicsItem | None:
        # DXF lines can be thinner than one screen pixel. Search a small,
        # screen-based tolerance around the click so the user does not have to
        # hit the mathematical centreline exactly.
        offsets = (
            QPoint(0, 0),
            QPoint(-3, 0),
            QPoint(3, 0),
            QPoint(0, -3),
            QPoint(0, 3),
            QPoint(-3, -3),
            QPoint(3, -3),
            QPoint(-3, 3),
            QPoint(3, 3),
            QPoint(-6, 0),
            QPoint(6, 0),
            QPoint(0, -6),
            QPoint(0, 6),
        )
        matches: dict[str, tuple[tuple[int, int, int], QGraphicsItem]] = {}
        for offset_index, offset in enumerate(offsets):
            for stack_index, hit in enumerate(self.items(view_point + offset)):
                item = hit
                while item is not None and not item.data(ENTITY_HANDLE_ROLE):
                    item = item.parentItem()
                if item is None:
                    continue
                handle = str(item.data(ENTITY_HANDLE_ROLE) or "")
                entity_type = str(item.data(ENTITY_TYPE_ROLE) or "").upper()
                if (
                    not handle
                    or not item.isVisible()
                    or not (item.flags() & QGraphicsItem.ItemIsSelectable)
                    or (
                        self._selection_entity_filter is not None
                        and entity_type not in self._selection_entity_filter
                    )
                    or (
                        self._selection_allowed_handles is not None
                        and handle.strip().upper()
                        not in self._selection_allowed_handles
                    )
                ):
                    continue
                score = (
                    _HIT_TEST_PRIORITY.get(entity_type, 8),
                    offset_index,
                    stack_index,
                )
                previous = matches.get(handle)
                if previous is None or score < previous[0]:
                    matches[handle] = (score, item)
        if not matches:
            return None
        return min(matches.values(), key=lambda value: value[0])[1]

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt API
        scene_pos = self.mapToScene(event.position().toPoint())
        self.mouseScenePositionChanged.emit(scene_pos.x(), -scene_pos.y())
        if self._box_mode and self._box_origin is not None:
            self._update_box_preview(scene_pos)
        if self._panning and self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API
        if event.button() in (Qt.MiddleButton, Qt.RightButton) and self._panning:
            self._panning = False
            self._pan_start = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def fit_all(self) -> None:
        scene = self.scene()
        if scene is None:
            return
        visible_bounds = QRectF()
        has_visible_item = False
        for item in scene.items():
            if not item.isVisible():
                continue
            item_bounds = item.sceneBoundingRect()
            visible_bounds = (
                visible_bounds.united(item_bounds)
                if has_visible_item
                else QRectF(item_bounds)
            )
            has_visible_item = True
        if not has_visible_item or visible_bounds.isNull():
            return
        self.fitInView(
            visible_bounds.adjusted(-10, -10, 10, 10),
            Qt.KeepAspectRatio,
        )


def build_scene(
    entities: list[DxfEntityRecord],
    display_entities: Iterable[DxfEntityRecord] | None = None,
) -> SceneBuildResult:
    """Build a scene without mixing display-only data into engineering.

    ``entities`` remains the authoritative selectable/engineering collection.
    ``display_entities`` is a separate optional render stream: its items take
    part in layer visibility and scene bounds, but are never selectable and are
    deliberately absent from ``handle_to_record``/``handle_to_item``.

    Both streams may contain the same extracted proxy primitive while the
    strict proxy adapter is enabled. A stable ``raw['proxy_source_key']`` is
    therefore de-duplicated only after an engineering record was successfully
    rendered; an unsupported proxy parent must not hide usable display data.
    """

    scene = QGraphicsScene()
    scene.setItemIndexMethod(QGraphicsScene.BspTreeIndex)
    groups: dict[str, list[QGraphicsItem]] = {}
    handle_to_item: dict[str, QGraphicsItem] = {}
    handle_to_record = {record.handle: record for record in entities if record.handle}
    display_id_to_item: dict[str, QGraphicsItem] = {}
    rendered_engineering_proxy_keys: set[Any] = set()

    default_pen = QPen(_DEFAULT_GEOMETRY_COLOR)
    default_pen.setCosmetic(True)
    default_pen.setWidthF(0.8)
    text_brush = QBrush(_DEFAULT_TEXT_COLOR)

    for record in entities:
        record_pen, record_brush = _style_for_record(record, default_pen, text_brush)
        item = _item_for_record(record, record_pen, record_brush)
        if item is None:
            continue
        item.setData(ENTITY_HANDLE_ROLE, record.handle)
        item.setData(ENTITY_LAYER_ROLE, record.layer)
        item.setData(ENTITY_TYPE_ROLE, record.entity_type.upper())
        item.setToolTip(
            f"{record.entity_type} | Layer: {record.layer} | Handle: {record.handle or '-'}"
        )
        item.setFlag(QGraphicsItem.ItemIsSelectable, True)
        scene.addItem(item)
        _register_layer_visibility(item, record, groups)
        if record.handle:
            handle_to_item[record.handle] = item
        proxy_key = _proxy_source_key(record)
        if proxy_key is not None:
            rendered_engineering_proxy_keys.add(proxy_key)

    rendered_display_ids: set[Any] = set()
    for record in display_entities or ():
        proxy_key = _proxy_source_key(record)
        if proxy_key is not None and proxy_key in rendered_engineering_proxy_keys:
            continue
        display_identity = _display_identity(record)
        if display_identity is not None and display_identity in rendered_display_ids:
            continue
        record_pen, record_brush = _style_for_record(record, default_pen, text_brush)
        item = _item_for_record(record, record_pen, record_brush)
        if item is None:
            continue
        display_id = str(
            record.raw.get("display_id")
            or record.handle
            or record.raw.get("proxy_source_key")
            or record.raw.get("source_key")
            or ""
        )
        item.setData(ENTITY_LAYER_ROLE, record.layer)
        item.setData(ENTITY_TYPE_ROLE, record.entity_type.upper())
        item.setData(DISPLAY_SOURCE_ID_ROLE, display_id)
        item.setData(DISPLAY_ONLY_ROLE, True)
        item.setToolTip(_display_tooltip(record, display_id))
        _disable_display_interaction(item)
        scene.addItem(item)
        _register_layer_visibility(item, record, groups)
        if display_id:
            display_id_to_item.setdefault(display_id, item)
        if display_identity is not None:
            rendered_display_ids.add(display_identity)

    rect = scene.itemsBoundingRect()
    if not rect.isNull():
        scene.setSceneRect(rect.adjusted(-20, -20, 20, 20))
    return SceneBuildResult(
        scene=scene,
        layer_groups=groups,
        handle_to_item=handle_to_item,
        handle_to_record=handle_to_record,
        display_id_to_item=display_id_to_item,
        layer_visibility={layer: True for layer in groups},
    )


def _register_layer_visibility(
    item: QGraphicsItem,
    record: DxfEntityRecord,
    groups: dict[str, list[QGraphicsItem]],
) -> None:
    layers = _record_visibility_layers(record)
    item.setData(VISIBILITY_LAYERS_ROLE, layers)
    for layer in layers:
        groups.setdefault(layer, []).append(item)


def _record_visibility_layers(record: DxfEntityRecord) -> tuple[str, ...]:
    ordered: list[str] = []
    values = record.raw.get("visibility_layers")
    if isinstance(values, (list, tuple)):
        for value in values:
            layer = str(value or "0")
            if layer not in ordered:
                ordered.append(layer)
    effective = str(record.layer or "0")
    if effective not in ordered:
        ordered.append(effective)
    return tuple(ordered)


def _style_for_record(
    record: DxfEntityRecord,
    default_pen: QPen,
    default_text_brush: QBrush,
) -> tuple[QPen, QBrush]:
    """Resolve extractor-provided RGB and keep it readable on the canvas.

    BYLAYER/BYBLOCK inheritance belongs to the DXF extractor because the Qt
    scene no longer owns the ezdxf document. The renderer consumes the resolved
    ``raw['display_rgb']`` value and applies only a light-background contrast
    correction. Invalid or absent values retain the historical dark palette.
    """

    pen = QPen(default_pen)
    color = _display_color(record.raw.get("display_rgb"))
    if color is None:
        return pen, QBrush(default_text_brush)
    color = _ensure_display_contrast(color, _VIEW_BACKGROUND)
    pen.setColor(color)
    return pen, QBrush(color)


def _display_color(value: Any) -> QColor | None:
    color: QColor | None = None
    if isinstance(value, str):
        candidate = QColor(value.strip())
        if candidate.isValid():
            color = candidate
    elif isinstance(value, Mapping):
        channels = tuple(value.get(name) for name in ("r", "g", "b"))
        color = _color_from_channels(channels)
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        color = _color_from_channels(value[:3])
    elif isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= 0xFFFFFF:
            color = QColor(
                (value >> 16) & 0xFF,
                (value >> 8) & 0xFF,
                value & 0xFF,
            )
    if color is None or not color.isValid():
        return None
    color.setAlpha(255)
    return color


def _color_from_channels(values: Sequence[Any]) -> QColor | None:
    if len(values) < 3:
        return None
    try:
        channels = tuple(int(value) for value in values[:3])
    except (TypeError, ValueError):
        return None
    if any(value < 0 or value > 255 for value in channels):
        return None
    return QColor(*channels)


def _ensure_display_contrast(color: QColor, background: QColor) -> QColor:
    if _contrast_ratio(color, background) >= _MINIMUM_DISPLAY_CONTRAST:
        return QColor(color)
    # Darken toward black in deterministic steps. Scaling all channels retains
    # the source hue more faithfully than replacing a light CAD color with an
    # unrelated fallback color.
    for step in range(1, 21):
        factor = 1.0 - step / 20.0
        candidate = QColor(
            round(color.red() * factor),
            round(color.green() * factor),
            round(color.blue() * factor),
        )
        if _contrast_ratio(candidate, background) >= _MINIMUM_DISPLAY_CONTRAST:
            return candidate
    return QColor(_DEFAULT_GEOMETRY_COLOR)


def _contrast_ratio(left: QColor, right: QColor) -> float:
    lighter = max(_relative_luminance(left), _relative_luminance(right))
    darker = min(_relative_luminance(left), _relative_luminance(right))
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(color: QColor) -> float:
    def channel(value: int) -> float:
        normalized = value / 255.0
        return (
            normalized / 12.92
            if normalized <= 0.04045
            else ((normalized + 0.055) / 1.055) ** 2.4
        )

    return (
        0.2126 * channel(color.red())
        + 0.7152 * channel(color.green())
        + 0.0722 * channel(color.blue())
    )


def _proxy_source_key(record: DxfEntityRecord) -> Any | None:
    value = record.raw.get("proxy_source_key", record.raw.get("source_key"))
    if value is None or value == "":
        return None
    return _freeze_key(value)


def _display_identity(record: DxfEntityRecord) -> Any | None:
    value = record.raw.get("display_id") or record.handle
    if value is None or value == "":
        return None
    return _freeze_key(value)


def _freeze_key(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(
            sorted((str(key), _freeze_key(item)) for key, item in value.items())
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_key(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze_key(item) for item in value), key=repr))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _display_tooltip(record: DxfEntityRecord, display_id: str) -> str:
    parent_handle = str(
        record.raw.get("parent_proxy_handle")
        or record.raw.get("source_proxy_handle")
        or "-"
    )
    instance_id = str(
        record.raw.get("proxy_instance_id")
        or record.raw.get("instance_key")
        or "-"
    )
    return (
        f"{record.entity_type} | Layer: {record.layer} | "
        f"Proxy: {parent_handle} | Instance: {instance_id} | "
        f"Display ID: {display_id or '-'}"
    )


def _disable_display_interaction(item: QGraphicsItem) -> None:
    """Keep a display subtree transparent to engineering hit testing."""

    pending = [item]
    while pending:
        current = pending.pop()
        current.setFlag(QGraphicsItem.ItemIsSelectable, False)
        current.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        pending.extend(current.childItems())


def _text_item(
    value: object,
    insert: object,
    *,
    height: object,
    rotation: object,
    brush: QBrush,
    geometry: Mapping[str, Any] | None = None,
    entity_type: object = "TEXT",
) -> QGraphicsItem | None:
    point = _xy(insert)
    if point is None:
        return None
    layout = geometry or {}
    kind = str(entity_type or "TEXT").upper()

    height_value = _finite_number(height)
    if height_value is None or height_value <= 0.0:
        height_value = 1.0
    rotation_value = _finite_number(rotation)
    if rotation_value is None:
        rotation_value = 0.0

    direction = _xy(layout.get("text_direction"))
    if kind == "MTEXT" and direction is not None and math.hypot(*direction) > 1e-12:
        rotation_value = math.degrees(math.atan2(direction[1], direction[0]))

    # Keep the source record untouched for audit, but render AutoCAD
    # formatting escapes (for example %%U underline) as human text.
    display_text = decode_dxf_text(str(value or ""))
    if kind == "MTEXT":
        label: QGraphicsSimpleTextItem | QGraphicsTextItem = QGraphicsTextItem()
        label.document().setDocumentMargin(0.0)
        label.setPlainText(display_text)
        label.setDefaultTextColor(brush.color())
    else:
        label = QGraphicsSimpleTextItem(display_text)
        label.setBrush(brush)
    font = QFont("Arial")
    font.setPointSizeF(8.0)
    oblique = _finite_number(layout.get("oblique")) or 0.0
    if abs(oblique) > 1e-9:
        # Qt does not expose an arbitrary font-oblique angle; italic keeps the
        # source intent visible without distorting the placement anchors.
        font.setItalic(True)
    label.setFont(font)
    bounds = label.boundingRect()
    base_height = max(float(bounds.height()), 1e-9)
    uniform_scale = max(0.01, height_value / base_height)

    if kind == "MTEXT":
        width = _finite_number(layout.get("width"))
        if width is not None and width > 0.0:
            label.setTextWidth(width / uniform_scale)
            bounds = label.boundingRect()
        local_anchor = _mtext_local_anchor(
            bounds,
            layout.get("attachment_point", 1),
        )
        scene_anchor = point
        horizontal_stretch = 1.0
    else:
        halign = _integer(layout.get("halign"), default=0)
        valign = _integer(layout.get("valign"), default=0)
        align_point = _xy(layout.get("align_point"))
        is_two_point = halign in {3, 5} and align_point is not None
        scene_anchor = point
        if not is_two_point and (halign != 0 or valign != 0) and align_point is not None:
            scene_anchor = align_point
        local_anchor = _text_local_anchor(bounds, halign, valign)
        width_factor = _finite_number(layout.get("width_factor"))
        horizontal_stretch = (
            width_factor
            if width_factor is not None and width_factor > 0.0
            else 1.0
        )
        if is_two_point:
            delta_x = align_point[0] - point[0]
            delta_y = align_point[1] - point[1]
            target_width = math.hypot(delta_x, delta_y)
            if target_width > 1e-12 and bounds.width() > 1e-12:
                rotation_value = math.degrees(math.atan2(delta_y, delta_x))
                local_anchor = QPointF(bounds.left(), bounds.center().y())
                if halign == 3:  # ALIGNED: uniform scale changes height too.
                    uniform_scale = target_width / bounds.width()
                    horizontal_stretch = 1.0
                else:  # FIT: retain requested height, stretch only the width.
                    horizontal_stretch = target_width / (
                        bounds.width() * uniform_scale
                    )

    label.setTransformOriginPoint(local_anchor)
    if abs(horizontal_stretch - 1.0) > 1e-12:
        stretch = QTransform()
        stretch.translate(local_anchor.x(), local_anchor.y())
        stretch.scale(horizontal_stretch, 1.0)
        stretch.translate(-local_anchor.x(), -local_anchor.y())
        label.setTransform(stretch)
    label.setScale(uniform_scale)
    label.setRotation(-rotation_value)
    # Every transform above fixes local_anchor, so one translation places the
    # selected DXF anchor exactly in the inverted-y top-view scene.
    label.setPos(
        scene_anchor[0] - local_anchor.x(),
        -scene_anchor[1] - local_anchor.y(),
    )
    return label


def _integer(value: object, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text_local_anchor(bounds: QRectF, halign: int, valign: int) -> QPointF:
    if halign in {1, 4}:
        x = bounds.center().x()
    elif halign == 2:
        x = bounds.right()
    else:
        x = bounds.left()

    if valign == 3:
        y = bounds.top()
    elif valign == 2:
        y = bounds.center().y()
    elif valign == 1:
        y = bounds.bottom()
    else:
        # Preserve historical baseline-left placement for the overwhelmingly
        # common default TEXT while honoring explicit vertical alignment.
        y = bounds.top()
    return QPointF(x, y)


def _mtext_local_anchor(bounds: QRectF, attachment_point: object) -> QPointF:
    attachment = min(9, max(1, _integer(attachment_point, default=1)))
    column = (attachment - 1) % 3
    row = (attachment - 1) // 3
    x = (bounds.left(), bounds.center().x(), bounds.right())[column]
    y = (bounds.top(), bounds.center().y(), bounds.bottom())[row]
    return QPointF(x, y)


def _item_for_record(
    record: DxfEntityRecord,
    pen: QPen,
    text_brush: QBrush,
) -> QGraphicsItem | None:
    geometry = record.geometry
    kind = record.entity_type

    if kind == "LINE":
        start, end = geometry.get("start"), geometry.get("end")
        if not start or not end:
            return None
        item = QGraphicsLineItem(start[0], -start[1], end[0], -end[1])
        item.setPen(pen)
        return item

    if kind in {"LWPOLYLINE", "POLYLINE"}:
        path = _polyline_scene_path(geometry)
        if path is None or path.isEmpty():
            return None
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        return item

    if kind == "CIRCLE":
        center, radius = geometry.get("center"), geometry.get("radius")
        if not center or radius is None:
            return None
        item = QGraphicsEllipseItem(center[0] - radius, -center[1] - radius, 2 * radius, 2 * radius)
        item.setPen(pen)
        return item

    if kind == "ARC":
        path = _arc_scene_path(geometry)
        if path is None or path.isEmpty():
            return None
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        return item

    if kind == "ELLIPSE":
        path = _ellipse_scene_path(geometry)
        if path is None or path.isEmpty():
            return None
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        return item

    if kind == "SPLINE":
        path = _spline_scene_path(geometry)
        if path is None or path.isEmpty():
            return None
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        return item

    if kind in {"TEXT", "MTEXT", "BLOCK_ATTR_TEXT"}:
        render_fragments = geometry.get("render_fragments")
        if isinstance(render_fragments, (list, tuple)) and render_fragments:
            group = QGraphicsItemGroup()
            for fragment in render_fragments:
                if not isinstance(fragment, Mapping):
                    continue
                insert = fragment.get("insert")
                fragment_text = str(fragment.get("text") or "")
                if _xy(insert) is None or not fragment_text:
                    continue
                fragment_brush = text_brush
                fragment_color = _display_color(fragment.get("display_rgb"))
                if fragment_color is not None:
                    fragment_brush = QBrush(
                        _ensure_display_contrast(fragment_color, _VIEW_BACKGROUND)
                    )
                child = _text_item(
                    fragment_text,
                    insert,
                    height=fragment.get("height", fragment.get("char_height", 1.0)),
                    rotation=fragment.get("rotation", 0.0),
                    brush=fragment_brush,
                    geometry=fragment,
                    entity_type=fragment.get("entity_type", "TEXT"),
                )
                if child is not None:
                    group.addToGroup(child)
            if group.childItems():
                return group

        insert = geometry.get("insert")
        if not insert:
            return None
        return _text_item(
            record.text or "",
            insert,
            height=geometry.get("height", geometry.get("char_height", 1.0)),
            rotation=geometry.get("rotation", 0.0),
            brush=text_brush,
            geometry=geometry,
            entity_type=kind,
        )

    if kind == "POINT":
        location = geometry.get("location")
        if not location:
            return None
        radius = 1.5
        item = QGraphicsEllipseItem(location[0] - radius, -location[1] - radius, radius * 2, radius * 2)
        item.setPen(pen)
        return item

    if kind == "INSERT":
        if geometry.get("render_marker") is False:
            return None
        insert = geometry.get("insert")
        if not insert:
            return None
        size = 3.0
        path = QPainterPath(QPointF(insert[0] - size, -insert[1]))
        path.lineTo(insert[0] + size, -insert[1])
        path.moveTo(insert[0], -insert[1] - size)
        path.lineTo(insert[0], -insert[1] + size)
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        return item

    if kind == "LEADER":
        vertices = geometry.get("vertices", [])
        if len(vertices) < 2:
            return None
        path = QPainterPath(QPointF(vertices[0][0], -vertices[0][1]))
        for vertex in vertices[1:]:
            path.lineTo(vertex[0], -vertex[1])
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        return item

    if kind == "DIMENSION":
        group = QGraphicsItemGroup()
        measurement = geometry.get("measurement")
        fallback_measurement_text = ""
        if measurement is not None:
            try:
                value = float(measurement)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                from lion_rebar.dxf.vertical_shape_resolver import snap

                fallback_measurement_text = f"{snap(value):g}"
        # Resolved primitives create QGraphicsLineItem and
        # QGraphicsSimpleTextItem children.  The final group.childItems()
        # check selects them before the proxy fallback below.
        primitives = geometry.get("render_primitives") or []
        for primitive in primitives:
            primitive_kind = str(primitive.get("kind") or "").upper()
            if primitive_kind == "LINE":
                start, end = primitive.get("start"), primitive.get("end")
                if start and end:
                    line = QGraphicsLineItem(start[0], -start[1], end[0], -end[1])
                    line.setPen(pen)
                    group.addToGroup(line)
            elif primitive_kind == "ARC":
                center = primitive.get("center")
                radius = primitive.get("radius")
                if not center or radius is None:
                    continue
                start_angle = float(primitive.get("start_angle") or 0.0)
                end_angle = float(primitive.get("end_angle") or 0.0)
                sweep = (end_angle - start_angle) % 360
                path = QPainterPath()
                for index in range(max(8, int(sweep / 5) + 1) + 1):
                    angle = math.radians(start_angle + sweep * index / max(8, int(sweep / 5) + 1))
                    point = QPointF(
                        center[0] + float(radius) * math.cos(angle),
                        -(center[1] + float(radius) * math.sin(angle)),
                    )
                    path.moveTo(point) if index == 0 else path.lineTo(point)
                arc = QGraphicsPathItem(path)
                arc.setPen(pen)
                group.addToGroup(arc)
            elif primitive_kind == "ARROW":
                insert = primitive.get("insert")
                if not insert:
                    continue
                # Generated _OBLIQUE arrowheads are short ticks.  Rendering a
                # resolved tick at the source insert/rotation is more faithful
                # than showing an unrelated INSERT marker.
                length = max(1.0, float(primitive.get("scale") or 1.0))
                angle = math.radians(float(primitive.get("rotation") or 0.0) + 45.0)
                dx, dy = length * math.cos(angle), length * math.sin(angle)
                path = QPainterPath(QPointF(insert[0] - dx, -(insert[1] - dy)))
                path.lineTo(insert[0] + dx, -(insert[1] + dy))
                arrow = QGraphicsPathItem(path)
                arrow.setPen(pen)
                group.addToGroup(arrow)
            elif primitive_kind == "TEXT":
                insert = primitive.get("insert")
                label_text = str(primitive.get("text") or "").strip()
                if not insert or not label_text:
                    continue
                label = QGraphicsSimpleTextItem(decode_dxf_text(label_text))
                label.setBrush(QBrush(pen.color()))
                font = QFont("Arial")
                font.setPointSizeF(8.0)
                label.setFont(font)
                label.setPos(insert[0], -insert[1])
                label.setRotation(-float(primitive.get("rotation") or 0.0))
                height = max(0.01, float(primitive.get("height") or 1.0))
                bounds = label.boundingRect()
                if bounds.height() > 0:
                    label.setScale(height / bounds.height())
                group.addToGroup(label)
        if group.childItems():
            return group

        # Unsupported/proxy DIMENSION fallback.  Prefer its explicit text;
        # only when absent use the measured value.  Never replace "21" with
        # a raw 50/100 geometric distance.
        start, end = geometry.get("defpoint2"), geometry.get("defpoint3")
        anchor = geometry.get("text_midpoint") or geometry.get("defpoint")
        if start and end and start != end:
            line = QGraphicsLineItem(start[0], -start[1], end[0], -end[1])
            line.setPen(pen)
            group.addToGroup(line)
        display_text = str(geometry.get("display_text") or record.text or "").strip()
        if display_text == "<>":
            display_text = ""
        if not display_text:
            display_text = fallback_measurement_text
        if display_text and (anchor or (start and end)):
            if not anchor:
                anchor = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            label = QGraphicsSimpleTextItem(decode_dxf_text(display_text))
            label.setBrush(QBrush(pen.color()))
            font = QFont("Arial")
            font.setPointSizeF(8.0)
            label.setFont(font)
            span = math.hypot(end[0] - start[0], end[1] - start[1]) if start and end else 10.0
            bounds = label.boundingRect()
            if bounds.height() > 0:
                label.setScale(max(0.01, (max(span, 10.0) * 0.12) / bounds.height()))
            label.setPos(anchor[0], -anchor[1])
            group.addToGroup(label)
        return group if group.childItems() else None

    return None


def centerline_path_for_record(record: DxfEntityRecord) -> QPainterPath | None:
    """Return the drawable primitive centreline, never annotation bounds."""

    geometry = record.geometry
    kind = record.entity_type.upper()
    if kind == "LINE":
        start, end = geometry.get("start"), geometry.get("end")
        if not start or not end:
            return None
        path = QPainterPath(QPointF(float(start[0]), -float(start[1])))
        path.lineTo(float(end[0]), -float(end[1]))
        return path

    if kind in {"LWPOLYLINE", "POLYLINE"}:
        return _polyline_scene_path(geometry)

    if kind == "ARC":
        return _arc_scene_path(geometry)

    return None


def _polyline_scene_path(geometry: Mapping[str, Any]) -> QPainterPath | None:
    return _scene_path_from_curve(polyline_path_from_geometry(geometry))


def _arc_scene_path(geometry: Mapping[str, Any]) -> QPainterPath | None:
    return _scene_path_from_curve(arc_path_from_geometry(geometry))


def _scene_path_from_curve(source) -> QPainterPath | None:
    if source is None or not source.elements:
        return None
    first = source.elements[0].start
    path = QPainterPath(QPointF(float(first[0]), -float(first[1])))
    for element in source.elements:
        if element.kind is PathElementKind.LINE:
            path.lineTo(float(element.end[0]), -float(element.end[1]))
            continue
        center = element.center
        radius = element.radius_raw
        sweep = element.sweep_deg
        if center is None or radius is None or radius <= 0.0 or sweep is None:
            return None
        start_angle = math.degrees(
            math.atan2(
                float(element.start[1]) - float(center[1]),
                float(element.start[0]) - float(center[0]),
            )
        )
        path.arcTo(
            QRectF(
                float(center[0]) - float(radius),
                -float(center[1]) - float(radius),
                2.0 * float(radius),
                2.0 * float(radius),
            ),
            start_angle,
            float(sweep),
        )
    if source.closed:
        path.closeSubpath()
    return path


def _ellipse_scene_path(geometry: Mapping[str, Any]) -> QPainterPath | None:
    center = _xy(geometry.get("center"))
    major = _xy(geometry.get("major_axis"))
    if center is None or major is None or math.hypot(*major) <= 1e-12:
        return None
    minor = _xy(geometry.get("minor_axis"))
    if minor is None:
        try:
            ratio = float(geometry.get("ratio", 1.0))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(ratio) or abs(ratio) <= 1e-12:
            return None
        minor = (-major[1] * ratio, major[0] * ratio)

    start_param = _finite_number(
        geometry.get("start_param", geometry.get("start_parameter", 0.0))
    )
    end_param = _finite_number(
        geometry.get("end_param", geometry.get("end_parameter", math.tau))
    )
    if start_param is None or end_param is None:
        return None
    raw_sweep = end_param - start_param
    if abs(raw_sweep) >= math.tau - 1e-9:
        sweep = math.tau if raw_sweep >= 0.0 else -math.tau
    else:
        sweep = raw_sweep % math.tau
    if abs(sweep) <= 1e-12:
        return None

    start_degrees = math.degrees(start_param)
    sweep_degrees = math.degrees(sweep)
    unit = QPainterPath(QPointF(math.cos(start_param), -math.sin(start_param)))
    unit.arcTo(
        QRectF(-1.0, -1.0, 2.0, 2.0),
        start_degrees,
        sweep_degrees,
    )
    if abs(abs(sweep) - math.tau) <= 1e-9:
        unit.closeSubpath()

    # A Qt unit arc uses (cos(t), -sin(t)). Map it to the original DXF
    # ellipse and fold the viewer's y-axis inversion into the same transform.
    transform = QTransform(
        major[0],
        -major[1],
        -minor[0],
        minor[1],
        center[0],
        -center[1],
    )
    return transform.map(unit)


def _spline_scene_path(geometry: Mapping[str, Any]) -> QPainterPath | None:
    """Render portable spline data when the extractor supplies it.

    ``bezier_segments`` preserves cubic curves. ``render_points`` and
    ``flattened_points`` are accepted as extractor-controlled display
    approximations; arbitrary NURBS control polygons are intentionally not
    connected because that would draw a different curve.
    """

    segments = geometry.get("bezier_segments")
    if isinstance(segments, (list, tuple)) and segments:
        first = segments[0]
        start = _xy(first.get("start")) if isinstance(first, Mapping) else None
        if start is None:
            start = _xy(geometry.get("start"))
        if start is not None:
            path = QPainterPath(QPointF(start[0], -start[1]))
            for segment in segments:
                if not isinstance(segment, Mapping):
                    return None
                control1 = _xy(segment.get("control1") or segment.get("c1"))
                control2 = _xy(segment.get("control2") or segment.get("c2"))
                end = _xy(segment.get("end"))
                if control1 is None or control2 is None or end is None:
                    return None
                path.cubicTo(
                    QPointF(control1[0], -control1[1]),
                    QPointF(control2[0], -control2[1]),
                    QPointF(end[0], -end[1]),
                )
            if geometry.get("closed"):
                path.closeSubpath()
            return path

    raw_points = geometry.get("render_points") or geometry.get("flattened_points")
    if not isinstance(raw_points, (list, tuple)):
        return None
    points = [point for value in raw_points if (point := _xy(value)) is not None]
    if len(points) < 2:
        return None
    path = QPainterPath(QPointF(points[0][0], -points[0][1]))
    for point in points[1:]:
        path.lineTo(point[0], -point[1])
    if geometry.get("closed"):
        path.closeSubpath()
    return path


def _xy(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
