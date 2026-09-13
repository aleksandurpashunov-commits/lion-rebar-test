from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.rebar_analyzer import RebarAnnotationAnalysis


class EntityPanel(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        content = QWidget()
        self.type_label = QLabel("—")
        self.layer_label = QLabel("—")
        self.handle_label = QLabel("—")
        self.block_label = QLabel("—")
        self.text_value = QPlainTextEdit()
        self.text_value.setReadOnly(True)
        self.text_value.setMaximumHeight(90)
        self.geometry_value = QPlainTextEdit()
        self.geometry_value.setReadOnly(True)
        self.geometry_value.setMaximumHeight(220)

        object_box = QGroupBox("DXF обект")
        object_layout = QFormLayout(object_box)
        object_layout.addRow("Тип:", self.type_label)
        object_layout.addRow("Layer:", self.layer_label)
        object_layout.addRow("Handle:", self.handle_label)
        object_layout.addRow("Block:", self.block_label)
        object_layout.addRow("Оригинален текст:", self.text_value)
        object_layout.addRow("Координати / геометрия:", self.geometry_value)

        self.analysis_status = QLabel("—")
        self.analysis_source = QLabel("—")
        self.analysis_position = QLabel("—")
        self.analysis_count = QLabel("—")
        self.analysis_diameter = QLabel("—")
        self.analysis_length = QLabel("—")
        self.analysis_spacing = QLabel("—")
        self.analysis_geometry = QLabel("—")
        self.analysis_notes = QLabel("—")
        for label in (
            self.analysis_status,
            self.analysis_source,
            self.analysis_position,
            self.analysis_count,
            self.analysis_diameter,
            self.analysis_length,
            self.analysis_spacing,
            self.analysis_geometry,
            self.analysis_notes,
        ):
            label.setWordWrap(True)
            label.setTextInteractionFlags(label.textInteractionFlags())

        analysis_box = QGroupBox("Автоматичен анализ — v0.2")
        analysis_layout = QFormLayout(analysis_box)
        analysis_layout.addRow("Статус:", self.analysis_status)
        analysis_layout.addRow("Източник:", self.analysis_source)
        analysis_layout.addRow("Позиция:", self.analysis_position)
        analysis_layout.addRow("Брой:", self.analysis_count)
        analysis_layout.addRow("Диаметър:", self.analysis_diameter)
        analysis_layout.addRow("Дължина:", self.analysis_length)
        analysis_layout.addRow("Стъпка:", self.analysis_spacing)
        analysis_layout.addRow("Геометрия:", self.analysis_geometry)
        analysis_layout.addRow("Контрол:", self.analysis_notes)

        layout = QVBoxLayout(content)
        layout.addWidget(object_box)
        layout.addWidget(analysis_box)
        layout.addStretch(1)
        self.setWidget(content)

    def clear(self) -> None:
        self.type_label.setText("—")
        self.layer_label.setText("—")
        self.handle_label.setText("—")
        self.block_label.setText("—")
        self.text_value.clear()
        self.geometry_value.clear()
        self._clear_analysis()

    def show_record(
        self,
        record: DxfEntityRecord,
        analysis: RebarAnnotationAnalysis | None = None,
    ) -> None:
        self.type_label.setText(record.entity_type)
        self.layer_label.setText(record.layer)
        self.handle_label.setText(record.handle or "—")
        self.block_label.setText(record.block_name or "—")
        self.text_value.setPlainText(record.text or "")
        self.geometry_value.setPlainText(
            json.dumps(record.geometry, ensure_ascii=False, indent=2)
        )
        self._show_analysis(analysis)

    def _clear_analysis(self) -> None:
        for label in (
            self.analysis_status,
            self.analysis_source,
            self.analysis_position,
            self.analysis_count,
            self.analysis_diameter,
            self.analysis_length,
            self.analysis_spacing,
            self.analysis_geometry,
            self.analysis_notes,
        ):
            label.setText("—")

    def _show_analysis(self, analysis: RebarAnnotationAnalysis | None) -> None:
        self._clear_analysis()
        if analysis is None or not analysis.recognized:
            self.analysis_status.setText("Не е разпознат армировъчен надпис")
            return

        parsed = analysis.parsed
        self.analysis_status.setText(
            "ЧАСТИЧНО РАЗПОЗНАТО — изисква проверка"
            if analysis.partial
            else "РАЗПОЗНАТО"
        )

        self.analysis_source.setText(analysis.source_description)

        if analysis.has_position_attribute:
            self.analysis_position.setText("Прочетена от отделен позиционен атрибут")
        elif analysis.source_kind == "BLOCK_ATTRIBUTES":
            # Уточнение: това се отнася за НОМЕРА НА ПОЗИЦИЯТА. Кръгчетата
            # от легендата (5a, 5b...) са друго нещо -- те дават Ø и
            # дължина и СЕ ползват, но само при точно съвпадение с надписа.
            self.analysis_position.setText(
                "НЯМА позиционен номер в блока. Близки цифри не се приемат за "
                "номер. (Легендните кръгчета 5a/5b се четат отделно и дават "
                "Ø и дължина.)"
            )
        else:
            self.analysis_position.setText(
                "Не се определя по близост; нужна е доказана проектантска връзка"
            )

        if parsed.count is None:
            self.analysis_count.setText("Липсва в избрания текст")
        elif len(parsed.count_factors) > 1:
            factors = " × ".join(str(value) for value in parsed.count_factors)
            self.analysis_count.setText(f"{parsed.count} бр. = {factors}")
        else:
            self.analysis_count.setText(f"{parsed.count} бр.")

        self.analysis_diameter.setText(
            f"Ø{_format_number(parsed.diameter_mm)} mm"
            if parsed.diameter_mm is not None
            else "Не е разпознат"
        )

        if parsed.length_cm is not None:
            self.analysis_length.setText(f"{_format_number(parsed.length_cm)} cm")
        elif parsed.length_expression:
            fixed = ""
            if parsed.fixed_addition_cm is not None:
                fixed = f"; известна добавка {_format_number(parsed.fixed_addition_cm)} cm"
            symbols = ", ".join(parsed.base_length_symbols) or "неизвестна база"
            self.analysis_length.setText(
                f"{parsed.length_expression}{fixed}; липсва размер за {symbols}"
            )
        else:
            self.analysis_length.setText("Не е разпозната")

        if analysis.has_spacing_attribute:
            self.analysis_spacing.setText("Прочетена от отделен атрибут")
        else:
            self.analysis_spacing.setText(
                "НЕ Е СВЪРЗАНА — близък текст s=... не се приема автоматично"
            )

        self.analysis_geometry.setText(
            "Свързана" if analysis.geometry_linked else "НЕ Е СВЪРЗАНА в тази версия"
        )

        notes = list(parsed.warnings)
        notes.append("Позиция и стъпка не се извеждат от геометрична близост.")
        if not analysis.geometry_linked:
            notes.append("Не използвай резултата за заявка или спецификация без геометрична връзка.")
        self.analysis_notes.setText(" ".join(notes) if notes else "Няма предупреждения")


def _format_number(value: float | None) -> str:
    if value is None:
        return "—"
    return str(int(value)) if float(value).is_integer() else str(value)
