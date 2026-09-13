from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition


class _PreviewSegmentItem(QGraphicsPathItem):
    """Clickable preview segment which delegates selection to the view."""

    def __init__(self, path: QPainterPath, *, key: str, owner: "ShapePreview"):
        super().__init__(path)
        self.key = key
        self.owner = owner
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.owner.select_segment(self.key, emit_signal=True)
            event.accept()
            return
        super().mousePressEvent(event)


class ShapePreview(QGraphicsView):
    """Readable MASTER preview with live, production-valued dimensions.

    The MASTER segment order and orientation are never rewritten.  A view-only
    rotation/mirror can still be applied, while the stable parameter codes stay
    attached to their original MASTER segments.
    """

    segmentSelected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setBackgroundBrush(QColor("#fafbfc"))
        self.setMinimumHeight(260)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._shape: ShapeDefinition | None = None
        self._rotation_deg = 0.0
        self._mirror = False
        self._segment_values_cm: tuple[float | None, ...] = ()
        self._segment_sources: tuple[str, ...] = ()
        self._total_length_cm: float | None = None
        self._status_text = ""
        self._selected_key = ""
        self._segment_items: dict[str, list[_PreviewSegmentItem]] = {}
        self._last_scene_rect = QRectF()

    def set_shape(self, shape: ShapeDefinition | None) -> None:
        self._shape = shape.clone() if shape is not None else None
        self._selected_key = ""
        self._render()

    def set_production_values(
        self,
        values_cm: Sequence[object] | None,
        *,
        sources: Sequence[str] | None = None,
        total_length_cm: object = None,
        status_text: str = "",
    ) -> None:
        self._segment_values_cm = tuple(_optional_positive(value) for value in (values_cm or ()))
        self._segment_sources = tuple(str(value or "") for value in (sources or ()))
        self._total_length_cm = _optional_positive(total_length_cm)
        self._status_text = str(status_text or "").strip()
        self._render()

    def set_view_transform(self, rotation_deg: float = 0.0, mirror: bool = False) -> None:
        self._rotation_deg = float(rotation_deg)
        self._mirror = bool(mirror)
        self._render()

    def select_segment(self, key: str, *, emit_signal: bool = False) -> None:
        normalized = str(key or "").strip().upper()
        if normalized not in self._segment_items:
            return
        self._selected_key = normalized
        self._apply_segment_pens()
        if emit_signal:
            self.segmentSelected.emit(normalized)

    @property
    def selected_segment(self) -> str:
        return self._selected_key

    def _render(self) -> None:
        self._scene.clear()
        self._segment_items.clear()
        shape = self._shape
        if shape is None or not shape.segments:
            message = self._scene.addSimpleText("Няма избрана MASTER форма")
            message.setBrush(QColor("#6b7280"))
            self._fit_scene()
            return

        segments = sorted(shape.segments, key=lambda item: item.index)
        draw_lengths = _normalized_draw_lengths(segments, self._segment_values_cm)
        heading = _heading_for_initial(shape.initial_direction)
        raw_point = QPointF(0.0, 0.0)
        raw_records: list[tuple[SegmentDefinition, QPointF, QPointF, QPainterPath]] = []

        for position, segment in enumerate(segments):
            absolute = _heading_for_drawing_direction(segment.drawing_direction)
            if absolute is not None:
                heading = absolute
            radians = math.radians(heading)
            length = draw_lengths[position]
            end = QPointF(
                raw_point.x() + length * math.cos(radians),
                raw_point.y() - length * math.sin(radians),
            )
            transformed_start = self._transform_point(raw_point)
            transformed_end = self._transform_point(end)
            path = _segment_path(
                transformed_start,
                transformed_end,
                curved=_is_curved_segment(shape, segment),
                mirror=self._mirror,
            )
            raw_records.append((segment, transformed_start, transformed_end, path))
            raw_point = end
            if segment.bend_after_deg is not None:
                direction = str(segment.bend_direction or "RIGHT").upper()
                sign = 1.0 if direction in {"LEFT", "НАЛЯВО"} else -1.0
                heading += sign * float(segment.bend_after_deg)

        for segment, start, end, path in raw_records:
            key = str(segment.length_code or f"S{segment.index}").strip().upper()
            item = _PreviewSegmentItem(path, key=key, owner=self)
            item.setToolTip(f"{key}: избери свързаното поле")
            item.setZValue(10)
            self._scene.addItem(item)
            self._segment_items.setdefault(key, []).append(item)

        if shape.closed and raw_records:
            first = raw_records[0][1]
            last = raw_records[-1][2]
            if math.hypot(first.x() - last.x(), first.y() - last.y()) > 1.0:
                closing = QGraphicsLineItem(last.x(), last.y(), first.x(), first.y())
                closing_pen = QPen(QColor("#64748b"), 1.4, Qt.PenStyle.DashLine)
                closing_pen.setCosmetic(True)
                closing.setPen(closing_pen)
                closing.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self._scene.addItem(closing)

        self._apply_segment_pens()
        occupied: list[QRectF] = []
        labelled_keys: set[str] = set()
        for position, (segment, start, end, _path) in enumerate(raw_records):
            key = str(segment.length_code or f"S{segment.index}").strip().upper()
            if key in labelled_keys:
                continue
            labelled_keys.add(key)
            self._add_dimension_label(
                segment,
                start,
                end,
                position=position,
                occupied=occupied,
            )

        drawing_rect = self._items_rect_without_text()
        transform_text = "MASTER ориентация"
        if self._rotation_deg:
            transform_text += f"; изглед {self._rotation_deg:g}°"
        if self._mirror:
            transform_text += "; огледален изглед"
        title = self._scene.addSimpleText(
            f"{shape.code} | {transform_text} | каноничен ред"
        )
        title.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        title.setBrush(QColor("#111827"))
        title.setPos(drawing_rect.left(), drawing_rect.top() - 45)

        total = f"L = {self._total_length_cm:g} cm" if self._total_length_cm is not None else "L = липсва"
        footer = self._scene.addSimpleText(
            f"СХЕМАТИЧНО | {total}"
        )
        footer.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        footer.setBrush(QColor("#166534"))
        footer.setPos(drawing_rect.left(), drawing_rect.bottom() + 28)
        if self._status_text:
            status = self._scene.addSimpleText(self._status_text)
            status.setFont(QFont("Arial", 9))
            status.setBrush(QColor("#9a3412" if "не " in self._status_text.lower() else "#1e3a8a"))
            status.setPos(drawing_rect.left(), drawing_rect.bottom() + 50)

        self._fit_scene()

    def _add_dimension_label(
        self,
        segment: SegmentDefinition,
        start: QPointF,
        end: QPointF,
        *,
        position: int,
        occupied: list[QRectF],
    ) -> None:
        key = str(segment.length_code or f"S{segment.index}").strip().upper()
        value = (
            self._segment_values_cm[position]
            if position < len(self._segment_values_cm)
            else _optional_positive(segment.fixed_length_cm)
        )
        source = self._segment_sources[position] if position < len(self._segment_sources) else ""
        value_text = f"{value:g} cm" if value is not None else "липсва"
        linked_count = len(self._segment_items.get(key, ()))
        text = QGraphicsSimpleTextItem(f"{key} = {value_text}")
        text.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        text.setBrush(QColor("#b91c1c" if value is None else "#1d4ed8"))
        tooltip = source or "Текуща производствена стойност"
        if linked_count > 1:
            tooltip += f"; един MASTER параметър за {linked_count} свързани сегмента"
        text.setToolTip(tooltip)
        self._scene.addItem(text)

        midpoint = QPointF((start.x() + end.x()) / 2.0, (start.y() + end.y()) / 2.0)
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        span = max(math.hypot(dx, dy), 1.0)
        nx, ny = -dy / span, dx / span
        side = _dimension_side(segment.dimension_position, position)
        base_offset = max(14.0, min(28.0, float(segment.dimension_offset or 6.0) * 2.5))
        label_rect = text.boundingRect()
        chosen = QPointF(midpoint.x(), midpoint.y())
        placed = QRectF()
        for attempt in range(24):
            offset = side * (base_offset + attempt * 18.0)
            candidate = QPointF(
                midpoint.x() + nx * offset - label_rect.width() / 2.0,
                midpoint.y() + ny * offset - label_rect.height() / 2.0,
            )
            placed = QRectF(candidate, label_rect.size()).adjusted(-4, -3, 4, 3)
            if not any(placed.intersects(existing) for existing in occupied):
                chosen = candidate
                break
            chosen = candidate
        occupied.append(placed)
        text.setPos(chosen)

        anchor = QPointF(
            chosen.x() + label_rect.width() / 2.0,
            chosen.y() + label_rect.height() / 2.0,
        )
        leader = QGraphicsLineItem(midpoint.x(), midpoint.y(), anchor.x(), anchor.y())
        leader_pen = QPen(QColor("#93a4b8"), 0.9, Qt.PenStyle.DotLine)
        leader_pen.setCosmetic(True)
        leader.setPen(leader_pen)
        leader.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        leader.setZValue(1)
        self._scene.addItem(leader)

    def _apply_segment_pens(self) -> None:
        for key, items in self._segment_items.items():
            selected = key == self._selected_key
            pen = QPen(QColor("#ea580c" if selected else "#172033"))
            pen.setWidthF(4.4 if selected else 2.8)
            pen.setCosmetic(True)
            for item in items:
                item.setPen(pen)

    def _items_rect_without_text(self) -> QRectF:
        rect = QRectF()
        for items in self._segment_items.values():
            for item in items:
                rect = item.sceneBoundingRect() if rect.isNull() else rect.united(item.sceneBoundingRect())
        return rect if not rect.isNull() else self._scene.itemsBoundingRect()

    def _fit_scene(self) -> None:
        rect = self._scene.itemsBoundingRect().adjusted(-34, -30, 34, 30)
        if rect.isNull():
            rect = QRectF(-100, -60, 200, 120)
        self._last_scene_rect = rect
        self._scene.setSceneRect(rect)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if not self._last_scene_rect.isNull():
            self.fitInView(self._last_scene_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _transform_point(self, point: QPointF) -> QPointF:
        x = -point.x() if self._mirror else point.x()
        y = point.y()
        radians = math.radians(self._rotation_deg)
        return QPointF(
            x * math.cos(radians) - y * math.sin(radians),
            x * math.sin(radians) + y * math.cos(radians),
        )


def _normalized_draw_lengths(
    segments: Sequence[SegmentDefinition],
    values: Sequence[float | None],
) -> tuple[float, ...]:
    known = [
        value
        for index, segment in enumerate(segments)
        for value in [
            values[index] if index < len(values) else _optional_positive(segment.fixed_length_cm)
        ]
        if value is not None
    ]
    median = sorted(known)[len(known) // 2] if known else 1.0
    result = []
    for index, segment in enumerate(segments):
        value = values[index] if index < len(values) else _optional_positive(segment.fixed_length_cm)
        if value is None:
            result.append(105.0)  # schematic extent only; never shown as a measurement
        else:
            result.append(max(62.0, min(185.0, 105.0 * value / max(median, 0.001))))
    return tuple(result)


def _segment_path(
    start: QPointF,
    end: QPointF,
    *,
    curved: bool,
    mirror: bool,
) -> QPainterPath:
    path = QPainterPath(start)
    if not curved:
        path.lineTo(end)
        return path
    dx = end.x() - start.x()
    dy = end.y() - start.y()
    span = max(math.hypot(dx, dy), 1.0)
    nx, ny = -dy / span, dx / span
    bulge = span * (0.48 if not mirror else -0.48)
    control1 = QPointF(start.x() - nx * bulge, start.y() - ny * bulge)
    control2 = QPointF(end.x() - nx * bulge, end.y() - ny * bulge)
    path.cubicTo(control1, control2, end)
    return path


def _is_curved_segment(shape: ShapeDefinition, segment: SegmentDefinition) -> bool:
    if str(segment.segment_type or "").upper() == "ARC":
        return True
    notes = str(segment.notes or "").upper()
    return str(shape.code).upper() == "F007" and "КРИВ" in notes


def _dimension_side(value: str | None, position: int) -> float:
    normalized = str(value or "AUTO").upper()
    if normalized in {"ОТГОРЕ", "ВЛЯВО", "TOP", "LEFT"}:
        return -1.0
    if normalized in {"ОТДОЛУ", "ВДЯСНО", "BOTTOM", "RIGHT"}:
        return 1.0
    return -1.0 if position % 2 == 0 else 1.0


def _optional_positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _heading_for_initial(value: str | None) -> float:
    mapping = {
        "RIGHT": 0.0,
        "НАДЯСНО": 0.0,
        "UP": 90.0,
        "НАГОРЕ": 90.0,
        "LEFT": 180.0,
        "НАЛЯВО": 180.0,
        "DOWN": -90.0,
        "НАДОЛУ": -90.0,
    }
    return mapping.get(str(value or "RIGHT").upper(), 0.0)


def _heading_for_drawing_direction(value: str | None) -> float | None:
    if not value:
        return None
    normalized = str(value).upper().replace("-", "_").replace(" ", "_")
    mapping = {
        "RIGHT": 0.0,
        "НАДЯСНО": 0.0,
        "UP_RIGHT": 45.0,
        "НАГОРЕ_НАДЯСНО": 45.0,
        "UP": 90.0,
        "НАГОРЕ": 90.0,
        "UP_LEFT": 135.0,
        "НАГОРЕ_НАЛЯВО": 135.0,
        "LEFT": 180.0,
        "НАЛЯВО": 180.0,
        "DOWN_LEFT": -135.0,
        "НАДОЛУ_НАЛЯВО": -135.0,
        "DOWN": -90.0,
        "НАДОЛУ": -90.0,
        "DOWN_RIGHT": -45.0,
        "НАДОЛУ_НАДЯСНО": -45.0,
    }
    return mapping.get(normalized)
