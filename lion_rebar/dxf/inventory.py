from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.text_parser import parse_rebar_text


@dataclass(frozen=True)
class RebarInventoryRow:
    sequence: int
    handle: str
    parent_insert_handle: str
    block_name: str
    layer: str
    label: str
    count: int | None
    diameter_mm: float | None
    length_cm: float | None
    total_length_m: float | None
    x: float | None
    y: float | None
    status: str
    source: str = "DIRECT_BJ_FI_L"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DiameterInventorySummary:
    diameter_mm: float
    annotation_count: int = 0
    bar_count: int = 0
    total_length_m: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RebarInventorySummary:
    annotation_count: int = 0
    complete_count: int = 0
    incomplete_count: int = 0
    total_bar_count: int = 0
    total_length_m: float = 0.0
    by_diameter: list[DiameterInventorySummary] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "annotation_count": self.annotation_count,
            "complete_count": self.complete_count,
            "incomplete_count": self.incomplete_count,
            "total_bar_count": self.total_bar_count,
            "total_length_m": self.total_length_m,
            "by_diameter": [item.to_dict() for item in self.by_diameter],
        }


def build_rebar_inventory(
    entities: list[DxfEntityRecord],
) -> tuple[list[RebarInventoryRow], RebarInventorySummary]:
    """Build a raw inventory only from verified block attributes BJ/FI/L.

    This is intentionally not a position list. Every row represents one
    ARM_N block reference from the DXF and keeps its original INSERT handle.
    No geometry, spacing, zone, or position is inferred.
    """

    candidates = [
        record
        for record in entities
        if record.entity_type == "BLOCK_ATTR_TEXT"
        and str(record.raw.get("source", "")) == "INSERT_ATTRIBUTES"
    ]
    candidates.sort(key=_sort_key)

    rows: list[RebarInventoryRow] = []
    diameter_totals: dict[float, DiameterInventorySummary] = {}

    for sequence, record in enumerate(candidates, start=1):
        parsed = parse_rebar_text(record.text or "")
        complete = (
            parsed.count is not None
            and parsed.diameter_mm is not None
            and parsed.length_cm is not None
            and not parsed.base_length_symbols
        )
        total_length_m = (
            parsed.count * parsed.length_cm / 100.0
            if complete
            else None
        )
        insert = record.geometry.get("insert", [])
        x = _safe_coordinate(insert, 0)
        y = _safe_coordinate(insert, 1)
        parent_handle = str(
            record.raw.get("parent_insert_handle")
            or record.geometry.get("parent_insert_handle")
            or ""
        )
        row = RebarInventoryRow(
            sequence=sequence,
            handle=record.handle,
            parent_insert_handle=parent_handle,
            block_name=record.block_name or "",
            layer=record.layer,
            label=record.text or "",
            count=parsed.count,
            diameter_mm=parsed.diameter_mm,
            length_cm=parsed.length_cm,
            total_length_m=total_length_m,
            x=x,
            y=y,
            status="ПЪЛЕН BJ/FI/L" if complete else "НЕПЪЛЕН — ЗА ПРОВЕРКА",
        )
        rows.append(row)

        if complete:
            diameter = float(parsed.diameter_mm)
            item = diameter_totals.setdefault(
                diameter,
                DiameterInventorySummary(diameter_mm=diameter),
            )
            item.annotation_count += 1
            item.bar_count += int(parsed.count)
            item.total_length_m += float(total_length_m)

    complete_count = sum(row.status == "ПЪЛЕН BJ/FI/L" for row in rows)
    summary = RebarInventorySummary(
        annotation_count=len(rows),
        complete_count=complete_count,
        incomplete_count=len(rows) - complete_count,
        total_bar_count=sum(row.count or 0 for row in rows if row.total_length_m is not None),
        total_length_m=sum(row.total_length_m or 0.0 for row in rows),
        by_diameter=sorted(diameter_totals.values(), key=lambda item: item.diameter_mm),
    )
    return rows, summary


def export_rebar_inventory_csv(
    path: Path,
    rows: list[RebarInventoryRow],
    summary: RebarInventorySummary,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(
            [
                "№",
                "Надпис BJ/FI/L",
                "Брой",
                "Диаметър mm",
                "Дължина cm",
                "Обща дължина m",
                "INSERT handle",
                "Derived handle",
                "Block",
                "Layer",
                "X",
                "Y",
                "Контрол",
                "Източник",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.sequence,
                    row.label,
                    _csv_number(row.count),
                    _csv_number(row.diameter_mm),
                    _csv_number(row.length_cm),
                    _csv_number(row.total_length_m, decimals=3),
                    row.parent_insert_handle,
                    row.handle,
                    row.block_name,
                    row.layer,
                    _csv_number(row.x, decimals=3),
                    _csv_number(row.y, decimals=3),
                    row.status,
                    row.source,
                ]
            )

        writer.writerow([])
        writer.writerow(["ОБОБЩЕНИЕ"])
        writer.writerow(["ARM_N блокове", summary.annotation_count])
        writer.writerow(["Пълни BJ/FI/L", summary.complete_count])
        writer.writerow(["Непълни", summary.incomplete_count])
        writer.writerow(["Общо пръти", summary.total_bar_count])
        writer.writerow(["Обща дължина m", _csv_number(summary.total_length_m, decimals=3)])
        writer.writerow([])
        writer.writerow(["Диаметър mm", "ARM_N блокове", "Пръти", "Обща дължина m"])
        for item in summary.by_diameter:
            writer.writerow(
                [
                    _csv_number(item.diameter_mm),
                    item.annotation_count,
                    item.bar_count,
                    _csv_number(item.total_length_m, decimals=3),
                ]
            )
    return path


def _sort_key(record: DxfEntityRecord) -> tuple[float, float, str]:
    insert = record.geometry.get("insert", [])
    x = _safe_coordinate(insert, 0) or 0.0
    y = _safe_coordinate(insert, 1) or 0.0
    # Reading order: upper rows first, then left to right.
    return (-y, x, record.handle)


def _safe_coordinate(value, index: int) -> float | None:
    try:
        return float(value[index])
    except (TypeError, ValueError, IndexError):
        return None


def _csv_number(value, *, decimals: int | None = None) -> str:
    if value is None:
        return ""
    number = float(value)
    if decimals is not None:
        text = f"{number:.{decimals}f}"
    elif number.is_integer():
        text = str(int(number))
    else:
        text = str(number)
    return text.replace(".", ",")
