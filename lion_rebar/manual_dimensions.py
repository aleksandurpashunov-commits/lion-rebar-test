from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Sequence

DIMENSION_SOURCE_ANNOTATION = "ANNOTATION"
DIMENSION_SOURCE_DXF = "DXF_GEOMETRY"
DIMENSION_SOURCE_MANUAL = "MANUAL_SEGMENTS"


@dataclass(frozen=True)
class ManualDimensionResolution:
    source: str
    production_length_cm: float
    production_segment_lengths_cm: tuple[float, ...]
    declared_length_cm: float | None
    measured_geometry_length_cm: float

    @property
    def geometry_difference_cm(self) -> float:
        return self.measured_geometry_length_cm - self.production_length_cm

    def to_dict(self) -> dict:
        return asdict(self)


def parse_manual_segment_lengths(text: str) -> tuple[float, ...]:
    """Parse owner-entered segment lengths such as ``61 + 18 + 61``.

    Comma is accepted as a decimal separator when it is inside a number. Segment
    separators may be ``+``, ``;`` or whitespace. Values must be positive.
    """

    normalized = str(text or "").strip().replace("×", "+").replace("*", "+")
    if not normalized:
        raise ValueError("Въведи размерите на сегментите, например 61 + 18 + 61.")

    # Decimal comma is normalized only when surrounded by digits. Other commas
    # are treated as separators to remain convenient for copied lists.
    normalized = re.sub(r"(?<=\d),(?=\d)", ".", normalized)
    parts = [part for part in re.split(r"[+;\s,]+", normalized) if part]
    values: list[float] = []
    for raw in parts:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"Невалиден размер на сегмент: {raw}") from exc
        if not (value > 0.0):
            raise ValueError("Всички размери на сегментите трябва да са положителни.")
        values.append(value)
    if not values:
        raise ValueError("Не са намерени валидни размери на сегменти.")
    return tuple(values)


def resolve_manual_dimensions(
    *,
    source: str,
    declared_length_cm: float | int | str | None,
    measured_segment_lengths_cm: Sequence[float],
    manual_segment_text: str = "",
    expected_segment_count: int | None = None,
) -> ManualDimensionResolution:
    measured_segments = tuple(float(value) for value in measured_segment_lengths_cm)
    measured_total = sum(measured_segments)
    declared = _optional_positive_float(declared_length_cm)

    if source == DIMENSION_SOURCE_ANNOTATION:
        if declared is None:
            raise ValueError("Надписът няма валидна производствена дължина.")
        return ManualDimensionResolution(
            source=source,
            production_length_cm=declared,
            production_segment_lengths_cm=(),
            declared_length_cm=declared,
            measured_geometry_length_cm=measured_total,
        )

    if source == DIMENSION_SOURCE_DXF:
        if measured_total <= 0.0:
            raise ValueError("DXF геометрията няма валидна измерена дължина.")
        return ManualDimensionResolution(
            source=source,
            production_length_cm=measured_total,
            production_segment_lengths_cm=measured_segments,
            declared_length_cm=declared,
            measured_geometry_length_cm=measured_total,
        )

    if source == DIMENSION_SOURCE_MANUAL:
        manual = parse_manual_segment_lengths(manual_segment_text)
        if expected_segment_count and len(manual) != int(expected_segment_count):
            raise ValueError(
                f"Избрани са {expected_segment_count} геометрични сегмента, "
                f"а са въведени {len(manual)} производствени размера."
            )
        return ManualDimensionResolution(
            source=source,
            production_length_cm=sum(manual),
            production_segment_lengths_cm=manual,
            declared_length_cm=declared,
            measured_geometry_length_cm=measured_total,
        )

    raise ValueError(f"Неподдържан източник на размерите: {source}")


def length_mismatch_is_material(production_length_cm: float, geometry_length_cm: float) -> bool:
    tolerance = max(1.0, abs(float(production_length_cm)) * 0.005)
    return abs(float(geometry_length_cm) - float(production_length_cm)) > tolerance


def _optional_positive_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0.0 else None
