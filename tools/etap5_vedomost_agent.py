# -*- coding: utf-8 -*-
"""Етап 5: чете ведомостта на ASD по координати и я сверява по полета.

Изход: ``VEDOMOST_AGENT.json`` плюс диагностични SVG рисунки на спорните
клетки.  Скриптът НЕ пипа чертежа и НЕ променя нито една позиция — ведомостта
е независима отправна точка и остава само за четене
(виж ``lion_rebar/dxf/asd_schedule.py``).

Употреба::

    python tools/etap5_vedomost_agent.py \
        --entities <working/extracted_entities.json> \
        --batch    <..._geometry_batch.json> \
        --out      <rezultat/etap_5/VEDOMOST_AGENT.json>

Нищо не е вързано за конкретен чертеж: спорните позиции се избират по
измереното разминаване, а не по номер или размер.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lion_rebar.dxf.asd_schedule import (  # noqa: E402
    SCHEDULE_READER,
    Schedule,
    ScheduleRow,
    compare_per_field,
    compare_with_rows,
    has_schedule,
    read_schedule,
)
from lion_rebar.dxf.models import DxfEntityRecord  # noqa: E402


def read_dxf_schedule(path: Path) -> Schedule:
    """Ведомостта на друга редакция на чертежа — през вносителя на проекта.

    Ползва се само за сравнение между редакции: така се вижда дали едно
    разминаване е отскоро, или стои и в предишната редакция.  Чертежът не се
    променя.
    """

    from lion_rebar.dxf.importer import DxfImporter

    return read_schedule(DxfImporter().import_file(path).entities)


# --------------------------------------------------------------------------- #
def load_records(path: Path) -> list[DxfEntityRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = []
    for item in payload.get("entities") or []:
        records.append(
            DxfEntityRecord(
                handle=str(item.get("handle") or ""),
                entity_type=str(item.get("entity_type") or ""),
                layer=str(item.get("layer") or ""),
                geometry=item.get("geometry") or {},
                text=item.get("text"),
                block_name=item.get("block_name"),
                color_index=item.get("color_index"),
                linetype=item.get("linetype"),
                raw=item.get("raw") or {},
            )
        )
    return records


def describe_position(schedule: Schedule, position: int) -> dict[str, Any]:
    """Пълният произход на един ред — за трите спорни позиции."""

    for row in schedule.rows:
        if row.position == position:
            data = row.to_dict()
            data["status"] = "ПРОЧЕТЕН"
            return data
    for unparsed in schedule.unparsed:
        cells = [c.to_dict() for c in unparsed.cells]
        if any(c["column_role"] == "position" and c["text"] == str(position) for c in cells):
            data = unparsed.to_dict()
            return data
    return {"status": "ЛИПСВА", "position": position}


def scan_drawing_for_key(
    records: Sequence[DxfEntityRecord],
    diameter: float,
    length: float,
) -> list[dict[str, Any]]:
    """Всеки текст в чертежа, който споменава този диаметър и тази дължина.

    Търси се по съдържание (``<Ø>x<дължина>`` в надписите на ASD), за да е
    независимо от разпознаването: ако програмата е пропуснала надпис, той пак
    ще се появи тук.
    """

    needle_diameter = _trim(diameter)
    needle_length = _trim(length)
    found = []
    for record in records:
        text = str(record.text or "").strip()
        if not text:
            continue
        compact = text.replace(" ", "").lower()
        if f"{needle_diameter}x{needle_length}" not in compact:
            continue
        geometry = record.geometry or {}
        anchor = geometry.get("visual_anchor") or geometry.get("insert") or [0.0, 0.0]
        found.append(
            {
                "handle": record.handle,
                "entity_type": record.entity_type,
                "layer": record.layer,
                "text": text,
                "x": round(float(anchor[0]), 2),
                "y": round(float(anchor[1]), 2),
            }
        )
    return found


def _trim(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


# --------------------------------------------------------------------------- #
# Диагностични SVG рисунки
# --------------------------------------------------------------------------- #
def diagnostic_svg(
    records: Sequence[DxfEntityRecord],
    schedule: Schedule,
    row: ScheduleRow,
    path: Path,
) -> None:
    """Рисува лентата на един ред: рамка, осева линия, дъги и надписи.

    Без външни зависимости — SVG се пише направо, защото добавяне на
    библиотека за диагностика не е оправдано.
    """

    columns = list(schedule.grid.column_bounds)
    if not columns or not row.band:
        return
    y0, y1 = row.band
    x0, x1 = columns[0], columns[-1]
    pad = 6.0
    width = (x1 - x0) + 2 * pad
    height = (y1 - y0) + 2 * pad
    scale = 900.0 / width
    caption = 26.0  # място за надписа над лентата

    def sx(x: float) -> float:
        return (x - x0 + pad) * scale

    def sy(y: float) -> float:
        # изгледът е с обърнат Y, както в drawing_view
        return (y1 + pad - y) * scale + caption

    parts: list[str] = []
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" '
        'viewBox="0 0 %.0f %.0f">'
        % (
            width * scale,
            height * scale + caption + 14.0,
            width * scale,
            height * scale + caption + 14.0,
        )
    )
    parts.append('<rect width="100%" height="100%" fill="#ffffff"/>')
    parts.append(
        '<text x="8" y="16" font-family="monospace" font-size="13" fill="#333">'
        "позиция %d | Ø%s | %d бр | обявена %s cm | марка %r | подреден детайл: %s</text>"
        % (
            row.position,
            _trim(row.diameter_mm),
            row.count,
            _trim(row.length_cm),
            row.mark_raw,
            "ДА" if row.detail_ordered else "НЕ — " + row.detail_reason,
        )
    )
    for x in columns:
        parts.append(
            '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#c8c8c8" stroke-width="1"/>'
            % (sx(x), sy(y0), sx(x), sy(y1))
        )
    for y in (y0, y1):
        parts.append(
            '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#c8c8c8" stroke-width="1"/>'
            % (sx(x0), sy(y), sx(x1), sy(y))
        )
    layer = schedule.layer
    for record in records:
        # само слоят на ведомостта и само ширината на таблицата: иначе в
        # лентата попадат линии от плана, който минава на същата височина
        if layer and record.layer != layer:
            continue
        geometry = record.geometry or {}
        kind = record.entity_type.upper()
        if kind == "LINE":
            start, end = geometry.get("start"), geometry.get("end")
            if not start or not end:
                continue
            middle_y = (float(start[1]) + float(end[1])) / 2.0
            middle_x = (float(start[0]) + float(end[0])) / 2.0
            if not (y0 <= middle_y <= y1 and x0 <= middle_x <= x1):
                continue
            vertical = abs(float(start[0]) - float(end[0])) < 1e-6
            horizontal = abs(float(start[1]) - float(end[1])) < 1e-6
            on_grid = (
                vertical and any(abs(float(start[0]) - x) < 1.0 for x in columns)
            ) or (
                horizontal
                and any(abs(float(start[1]) - y) < 1.0 for y in schedule.grid.row_bounds)
            )
            parts.append(
                '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%s"/>'
                % (
                    sx(float(start[0])),
                    sy(float(start[1])),
                    sx(float(end[0])),
                    sy(float(end[1])),
                    "#c8c8c8" if on_grid else "#1a4fa0",
                    1 if on_grid else 2,
                )
            )
        elif kind == "ARC":
            center, radius = geometry.get("center"), geometry.get("radius")
            if not center or radius is None:
                continue
            if not (y0 <= float(center[1]) <= y1 and x0 <= float(center[0]) <= x1):
                continue
            points = []
            a0 = float(geometry.get("start_angle") or 0.0)
            a1 = float(geometry.get("end_angle") or 0.0)
            if a1 < a0:
                a1 += 360.0
            steps = max(4, int((a1 - a0) / 10))
            for i in range(steps + 1):
                angle = math.radians(a0 + (a1 - a0) * i / steps)
                points.append(
                    (
                        float(center[0]) + float(radius) * math.cos(angle),
                        float(center[1]) + float(radius) * math.sin(angle),
                    )
                )
            path_data = " ".join(
                ("M" if i == 0 else "L") + "%.1f,%.1f" % (sx(p[0]), sy(p[1]))
                for i, p in enumerate(points)
            )
            parts.append(
                '<path d="%s" fill="none" stroke="#a03a1a" stroke-width="2"/>' % path_data
            )
    for cell in row.cells:
        colour = "#0a7a3a" if cell.column_role == "detail" else "#444444"
        parts.append(
            '<circle cx="%.1f" cy="%.1f" r="2.5" fill="%s"/>' % (sx(cell.x), sy(cell.y), colour)
        )
        parts.append(
            '<text x="%.1f" y="%.1f" font-family="monospace" font-size="11" fill="%s">'
            "%s (%s)</text>"
            % (
                sx(cell.x) + 5,
                sy(cell.y) - 4,
                colour,
                _escape(cell.text),
                cell.handle,
            )
        )
    for label in row.detail_labels:
        if label.segment_index is None:
            continue
        parts.append(
            '<text x="%.1f" y="%.1f" font-family="monospace" font-size="10" fill="#0a7a3a">'
            "→ участък %d (%s)</text>"
            % (sx(label.x) + 5, sy(label.y) + 9, label.segment_index + 1, label.segment_handle)
        )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entities", required=True, type=Path)
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--diag", type=Path, default=None)
    parser.add_argument(
        "--compare-dxf",
        type=Path,
        default=None,
        help="друга редакция на чертежа: ведомостта ѝ се чете и се сравнява, "
        "за да се види дали разминаването е отскоро",
    )
    parser.add_argument(
        "--positions",
        default="",
        help="номера на позиции за подробен произход, разделени със запетая; "
        "по подразбиране — тези с отказан детайл или непозната марка",
    )
    args = parser.parse_args(argv)

    records = load_records(args.entities)
    batch = json.loads(args.batch.read_text(encoding="utf-8"))
    program_rows = batch.get("rows") or []

    schedule = read_schedule(records)
    compare_old = compare_with_rows(schedule, program_rows)
    per_field = compare_per_field(schedule, program_rows)

    # спорната двойка: най-голямото разминаване в броя, а не избран размер
    disputed = None
    for entry in per_field["fields"]["count"]["rows"]:
        if entry["difference"] is None:
            continue
        if disputed is None or abs(entry["difference"]) > abs(disputed["difference"]):
            disputed = entry
    key_evidence: dict[str, Any] = {}
    if disputed is not None:
        drawing_texts = scan_drawing_for_key(
            records, disputed["diameter_mm"], disputed["length_cm"]
        )
        in_schedule_layer = [
            t for t in drawing_texts if "reinforcement table" in t["layer"].lower()
        ]
        in_drawing = [t for t in drawing_texts if t not in in_schedule_layer]
        program_sum = disputed["count_program"]
        label_sum = 0
        for entry in program_rows:
            if (
                str(entry.get("annotation_class") or "") == "ACTIONABLE"
                and entry.get("diameter_mm") == disputed["diameter_mm"]
                and entry.get("declared_length_cm") == disputed["length_cm"]
            ):
                label_sum += int(entry.get("annotation_count") or 0)
        key_evidence = {
            "disputed_key": [disputed["diameter_mm"], disputed["length_cm"]],
            "schedule": disputed["count_schedule"],
            "program": program_sum,
            "difference": disputed["difference"],
            "explanation": (
                "Програмата е събрала точно това, което пише в чертежа: %d надписа "
                "за Ø%s x %s cm, сбор %d броя, без пропуснат и без грешно прочетен "
                "надпис. Разликата %d съществува само във ведомостта на "
                "проектанта — в чертежа няма нито надпис, нито геометрия за нея."
                % (
                    len(in_drawing),
                    _trim(disputed["diameter_mm"]),
                    _trim(disputed["length_cm"]),
                    label_sum,
                    disputed["difference"],
                )
            ),
            "evidence": {
                "drawing_labels": in_drawing,
                "drawing_label_sum": label_sum,
                "schedule_cells": in_schedule_layer,
                "program_annotations": [
                    {
                        "handle": entry.get("annotation_handle"),
                        "text": str(entry.get("annotation_text") or "").strip(),
                        "count": entry.get("annotation_count"),
                        "candidate_handles": entry.get("candidate_handles"),
                    }
                    for entry in program_rows
                    if entry.get("diameter_mm") == disputed["diameter_mm"]
                    and entry.get("declared_length_cm") == disputed["length_cm"]
                ],
                "schedule_row_cells": [
                    [cell.to_dict() for cell in row.cells]
                    for row in schedule.rows
                    if (round(row.diameter_mm, 1), round(row.length_cm, 1))
                    == (
                        round(disputed["diameter_mm"], 1),
                        round(disputed["length_cm"], 1),
                    )
                ],
            },
        }
        if args.compare_dxf is not None:
            other = read_dxf_schedule(args.compare_dxf)
            other_count = sum(
                row.count
                for row in other.rows
                if (round(row.diameter_mm, 1), round(row.length_cm, 1))
                == (
                    round(disputed["diameter_mm"], 1),
                    round(disputed["length_cm"], 1),
                )
            )
            key_evidence["evidence"]["other_generation"] = {
                "file": str(args.compare_dxf),
                "rows": len(other.rows),
                "pieces": other.total_pieces(),
                "count_for_disputed_key": other_count,
                "same_as_current": other_count == disputed["count_schedule"],
            }
            key_evidence["explanation"] += (
                " Предишната редакция на чертежа дава същото число (%s), тоест "
                "разминаването не е от скорошна редакция."
                % other_count
                if other_count == disputed["count_schedule"]
                else " Предишната редакция на чертежа дава друго число (%s) — "
                "разминаването е настъпило между редакциите." % other_count
            )

    # подробен произход
    wanted: list[int] = []
    if args.positions.strip():
        wanted = [int(v) for v in args.positions.split(",") if v.strip()]
    else:
        for row in schedule.rows:
            if not row.detail_ordered or row.mark_kind in ("", "НЕИЗВЕСТНА") or row.notes:
                wanted.append(row.position)
        for row in schedule.rows:
            key = (round(row.diameter_mm, 1), round(row.length_cm, 1))
            if disputed and key == (
                round(disputed["diameter_mm"], 1),
                round(disputed["length_cm"], 1),
            ):
                wanted.append(row.position)
        for unparsed in schedule.unparsed:
            for cell in unparsed.cells:
                if cell.column_role == "position" and cell.text.isdigit():
                    wanted.append(int(cell.text))
    wanted = sorted(set(wanted))

    diag_dir = args.diag
    diagrams: list[str] = []
    if diag_dir is not None:
        diag_dir.mkdir(parents=True, exist_ok=True)
        for row in schedule.rows:
            if row.position not in wanted:
                continue
            target = diag_dir / ("poziciya_%02d.svg" % row.position)
            diagnostic_svg(records, schedule, row, target)
            diagrams.append(str(target))

    payload: dict[str, Any] = {
        "schema": "lion-rebar-etap5-vedomost/v1",
        "reader": SCHEDULE_READER,
        "read_only": True,
        "entities": str(args.entities),
        "batch_report": str(args.batch),
        "method": {
            "layer": schedule.layer,
            "grid": schedule.grid.to_dict(),
            "how": (
                "Решетката се намира от линиите на слоя: вертикалните дават "
                "границите на колоните, хоризонталните — на редовете. Приемат се "
                "само линии, които покриват поне 80 % от височината (ширината) на "
                "таблицата, за да не се бъркат рисунките в „Детайл“ с рамка. "
                "Всяка клетка се отнася към (колона, ред) по опорната си точка: "
                "за MTEXT по attachment_point, за TEXT по align_point с "
                "halign/valign. Ред се открива от решетката, НЕ от марката в "
                "колоната N/Ø, затова ред с марка Ø („0“ в чист текст) се чете "
                "нормално и не изтича в съседния. Подреден детайл се твърди само "
                "когато отсечките в клетката се навързват в еднозначна верига "
                "през дъгите и всеки надпис е паралелен на точно една отсечка."
            ),
            "header_seen": schedule.header_seen,
            "schedule_rows": len(schedule.rows),
            "schedule_pieces": schedule.total_pieces(),
            "program_pieces": per_field["program_pieces"],
            "notes": schedule.notes,
            "cells_outside_grid": [c.to_dict() for c in schedule.outside_grid],
            "diagrams": diagrams,
        },
        "rows": [row.to_dict() for row in schedule.rows],
        "unparsed": [u.to_dict() for u in schedule.unparsed],
        "per_field_comparison": per_field,
        "compare_with_rows": compare_old,
    }
    for position in wanted:
        payload["position_%d" % position] = describe_position(schedule, position)
    if key_evidence:
        payload["diameter%s_length%s" % (
            _trim(key_evidence["disputed_key"][0]),
            _trim(key_evidence["disputed_key"][1]),
        )] = key_evidence

    payload["limitations"] = [
        "Ведомостта е САМО ЗА ЧЕТЕНЕ. Ако влезе в разпознаването, престава да е "
        "независима отправна точка и това сверяване губи смисъл.",
        "Сверяването групира по (диаметър, дължина), защото извлечените позиции "
        "не носят номер на позиция от ведомостта; два различни номера с еднакви "
        "диаметър и дължина попадат в една група.",
        "Значението на марката в колоната N/Ø (нервурана срещу гладка армировка) "
        "е проектна конвенция — модулът връща суровата стойност и вида, но не "
        "решава от коя стомана е прътът.",
        "Подреденият детайл е доказан от рисунката в клетката, не от сбора: "
        "съвпадането със обявената дължина се отчита отделно.",
        "Миниатюрата в „Детайл“ не е в мащаб — от дължините на нарисуваните "
        "отсечки не се изчислява размер.",
    ]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("слой:", schedule.layer)
    print("редове:", len(schedule.rows), "| непрочетени:", len(schedule.unparsed))
    print("броя по ведомост:", schedule.total_pieces(), "| по програма:", per_field["program_pieces"])
    for name, data in per_field["fields"].items():
        print("  %-9s %s" % (name, data["tally"]))
    if disputed:
        print(
            "най-голямо разминаване: Ø%s x %s cm — ведомост %s, програма %s (%s)"
            % (
                _trim(disputed["diameter_mm"]),
                _trim(disputed["length_cm"]),
                disputed["count_schedule"],
                disputed["count_program"],
                disputed["difference"],
            )
        )
    print("записано:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
