from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget


_ROLE_BACKGROUNDS = {
    "REBAR_GEOMETRY": QColor("#dcfce7"),
    "REBAR_TEXT": QColor("#e0f2fe"),
    "STIRRUPS": QColor("#fef3c7"),
    "DIMENSIONS": QColor("#ede9fe"),
    "LEADERS": QColor("#f3e8ff"),
    "BEAM_GEOMETRY": QColor("#ffedd5"),
    "LEGEND": QColor("#ecfccb"),
    "USER_HELPER": QColor("#cffafe"),
    "FRAME": QColor("#f1f5f9"),
    "NOISE": QColor("#fee2e2"),
    "UNKNOWN": QColor("#f8fafc"),
}


class LayerPanel(QWidget):
    layerVisibilityChanged = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Филтър на слоевете…")
        self.list_widget = QListWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.search)
        layout.addWidget(self.list_widget)
        self.search.textChanged.connect(self._apply_filter)
        self.list_widget.itemChanged.connect(self._item_changed)

    def set_layers(
        self,
        layers: list[str],
        visibility: dict[str, bool] | None = None,
        classifications: dict[str, dict] | None = None,
    ) -> None:
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        visibility = visibility or {}
        classifications = classifications or {}
        for layer in layers:
            item = QListWidgetItem(layer)
            item.setData(Qt.ItemDataRole.UserRole, layer)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if visibility.get(layer, True) else Qt.CheckState.Unchecked
            )
            info = classifications.get(layer) or {}
            role = str(info.get("role") or "UNKNOWN")
            confidence = info.get("confidence")
            reasons = "\n".join(str(value) for value in info.get("reasons", []) if str(value))
            item.setToolTip(
                f"Роля: {role}\nУвереност: {confidence if confidence is not None else '—'}"
                + (f"\n{reasons}" if reasons else "")
            )
            background = _ROLE_BACKGROUNDS.get(role)
            if background is not None:
                item.setBackground(background)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        self._apply_filter(self.search.text())

    def visibility(self) -> dict[str, bool]:
        return {
            str(self.list_widget.item(index).data(Qt.ItemDataRole.UserRole) or self.list_widget.item(index).text()):
            self.list_widget.item(index).checkState() == Qt.CheckState.Checked
            for index in range(self.list_widget.count())
        }

    def _item_changed(self, item: QListWidgetItem) -> None:
        layer = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
        self.layerVisibilityChanged.emit(
            layer,
            item.checkState() == Qt.CheckState.Checked,
        )

    def _apply_filter(self, value: str) -> None:
        needle = value.casefold().strip()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            role = str(item.toolTip() or "")
            haystack = f"{item.text()} {role}".casefold()
            item.setHidden(bool(needle) and needle not in haystack)
