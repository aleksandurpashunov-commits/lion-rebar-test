# -*- coding: utf-8 -*-
"""Четене на армировъчната ведомост на ASD — по координати, не по ред на документа.

Всички образци тук са изкуствени: размерите, позициите и handle-ите са
измислени за теста, за да не се настройва логиката по конкретен чертеж.

Регресиите, които тези тестове пазят (всички бяха червени срещу четенето по
ред на документа):

* ред с марка, различна от ``N`` (символът Ø, който в чист текст остава „0“),
  се чете нормално;
* клетките на един ред не изтичат в съседния, дори когато редът на документа
  е разбъркан;
* колоната „Детайл“ е рисунка: подреден списък се твърди само когато всеки
  нарисуван участък има точно един надпис;
* сверяването отчита по полета и никога не дава голо „СЪВПАДА“.
"""
from __future__ import annotations

import json
import math

import pytest

from lion_rebar.dxf.asd_schedule import (
    Schedule,
    compare_per_field,
    compare_with_rows,
    has_schedule,
    read_schedule,
)
from lion_rebar.dxf.models import DxfEntityRecord

LAYER = "AutoCAD_Structural_Detailing_Reinforcement tables"

#: Границите на изкуствената таблица.  Шестте колони повтарят подредбата на
#: ASD: позиция | N/Ø | диаметър | брой | дължина | Детайл.
COLUMNS = (0.0, 100.0, 160.0, 300.0, 360.0, 560.0, 1000.0)
HEADERS = ("позиция", "N/Ø", "диаметър", "брой", "дължина (cm)", "Детайл (cm)")
HEADER_HEIGHT = 40.0
ROW_HEIGHT = 50.0
TABLE_TOP = 1000.0


class _Builder:
    """Сглобява слой с ведомост: решетка от линии плюс клетки."""

    def __init__(self, row_count: int) -> None:
        self.records: list[DxfEntityRecord] = []
        self._next = 0x1000
        self.bounds = [TABLE_TOP, TABLE_TOP - HEADER_HEIGHT]
        for index in range(row_count):
            self.bounds.append(TABLE_TOP - HEADER_HEIGHT - ROW_HEIGHT * (index + 1))
        bottom = self.bounds[-1]
        for x in COLUMNS:
            self._line((x, TABLE_TOP), (x, bottom))
        for y in self.bounds:
            self._line((COLUMNS[0], y), (COLUMNS[-1], y))
        for column, text in enumerate(HEADERS):
            self.cell(text, column, band=-1)

    # -- примитиви ------------------------------------------------------- #
    def handle(self) -> str:
        self._next += 1
        return format(self._next, "X")

    def _line(self, start, end) -> DxfEntityRecord:
        record = DxfEntityRecord(
            handle=self.handle(),
            entity_type="LINE",
            layer=LAYER,
            geometry={
                "start": [start[0], start[1], 0.0],
                "end": [end[0], end[1], 0.0],
            },
        )
        self.records.append(record)
        return record

    def band(self, index: int) -> tuple[float, float]:
        """``index`` -1 е заглавната лента, 0 и нататък са редовете."""

        if index < 0:
            return self.bounds[1], self.bounds[0]
        return self.bounds[index + 2], self.bounds[index + 1]

    def centre(self, column: int, index: int) -> tuple[float, float]:
        low, high = self.band(index)
        return (COLUMNS[column] + COLUMNS[column + 1]) / 2.0, (low + high) / 2.0

    def cell(self, text: str, column: int, band: int) -> DxfEntityRecord:
        x, y = self.centre(column, band)
        record = DxfEntityRecord(
            handle=self.handle(),
            entity_type="MTEXT",
            layer=LAYER,
            geometry={
                # ASD пише табличните клетки с attachment_point 5 (среда-център),
                # тоест insert Е центърът на клетката.
                "attachment_point": 5,
                "insert": [x, y, 0.0],
                "char_height": 10.0,
                "estimated_text_width": 8.0 * len(text),
                "estimated_text_height": 12.0,
                "rotation": 0.0,
            },
            text=text,
        )
        self.records.append(record)
        return record

    def label(self, text: str, x: float, y: float, rotation: float = 0.0) -> DxfEntityRecord:
        record = DxfEntityRecord(
            handle=self.handle(),
            entity_type="TEXT",
            layer=LAYER,
            geometry={
                "insert": [x - 4.0, y - 5.0, 0.0],
                "align_point": [x, y, 0.0],
                "halign": 1,
                "valign": 2,
                "height": 10.0,
                "rotation": rotation,
                "estimated_text_width": 8.0 * len(text),
                "estimated_text_height": 12.0,
            },
            text=text,
        )
        self.records.append(record)
        return record

    def arc(self, center, radius: float, start_angle: float, end_angle: float) -> None:
        self.records.append(
            DxfEntityRecord(
                handle=self.handle(),
                entity_type="ARC",
                layer=LAYER,
                geometry={
                    "center": [center[0], center[1], 0.0],
                    "radius": radius,
                    "start_angle": start_angle,
                    "end_angle": end_angle,
                },
            )
        )

    # -- цели редове ------------------------------------------------------ #
    def row(self, band: int, position, mark, diameter, count, length) -> None:
        for column, value in enumerate((position, mark, diameter, count, length)):
            if value is None:
                continue
            self.cell(str(value), column, band)

    def straight_detail(self, band: int, length_text: str) -> None:
        """Прав прът: една отсечка и един надпис над нея."""

        _, y = self.centre(5, band)
        x0, x1 = COLUMNS[5] + 40.0, COLUMNS[6] - 40.0
        self._line((x0, y), (x1, y))
        self.label(length_text, (x0 + x1) / 2.0, y + 14.0, rotation=0.0)

    def u_detail(self, band: int, texts: tuple[str, str, str]) -> None:
        """Три участъка, свързани през две дъги — както ASD рисува винкелите."""

        _, y = self.centre(5, band)
        left, right = COLUMNS[5] + 40.0, COLUMNS[6] - 90.0
        self._line((left, y - 10.0), (left, y + 10.0))
        self.arc((left + 5.0, y - 10.0), 5.0, 180.0, 270.0)
        self._line((left + 5.0, y - 15.0), (right - 5.0, y - 15.0))
        self.arc((right - 5.0, y - 10.0), 5.0, 270.0, 360.0)
        self._line((right, y - 10.0), (right, y + 10.0))
        self.label(texts[0], left - 12.0, y, rotation=90.0)
        self.label(texts[1], (left + right) / 2.0, y - 22.0, rotation=0.0)
        self.label(texts[2], right + 12.0, y, rotation=90.0)

    def scattered_detail(self, band: int, texts: tuple[str, ...]) -> None:
        """Пет нарисувани участъка, но по-малко надписи — както позиция 41.

        Надписите са на различни y около миниатюрата; това НЕ е подреден списък.
        """

        _, y = self.centre(5, band)
        points = [
            (COLUMNS[5] + 40.0, y - 15.0),
            (COLUMNS[5] + 60.0, y + 10.0),
            (COLUMNS[5] + 90.0, y - 10.0),
            (COLUMNS[5] + 140.0, y + 10.0),
            (COLUMNS[5] + 200.0, y - 10.0),
            (COLUMNS[5] + 260.0, y + 5.0),
        ]
        for start, end in zip(points, points[1:]):
            self._line(start, end)
        for offset, text in enumerate(texts):
            self.label(text, COLUMNS[5] + 70.0 + 40.0 * offset, y + 18.0 - 6.0 * offset)


def _straight_table() -> _Builder:
    builder = _Builder(3)
    builder.row(0, 1, "N", 8, 10, 300)
    builder.straight_detail(0, "300")
    builder.row(1, 2, "N", 10, 20, 320)
    builder.u_detail(1, ("10", "300", "10"))
    builder.row(2, 3, "N", 12, 7, 450)
    builder.straight_detail(2, "450")
    return builder


# --------------------------------------------------------------------------- #
# 1) нормален ред
# --------------------------------------------------------------------------- #
def test_normal_rows_are_read_with_provenance():
    schedule = read_schedule(_straight_table().records)

    assert schedule.header_seen is True
    assert schedule.layer == LAYER
    assert schedule.unparsed == []
    assert [r.position for r in schedule.rows] == [1, 2, 3]

    first = schedule.by_position()[1]
    assert first.diameter_mm == 8.0
    assert first.count == 10
    assert first.length_cm == 300.0
    assert first.mark_raw == "N"
    assert first.mark_kind == "N"
    assert first.detail_cm == (300.0,)
    assert first.detail_ordered is True

    # произходът на всяка клетка: handle, координати, колона и защо
    roles = {cell.column_role for cell in first.cells}
    assert {"position", "mark", "diameter", "count", "length", "detail"} == roles
    for cell in first.cells:
        assert cell.handle
        assert cell.row_index == first.row_index
        assert "колона" in cell.reason
    assert schedule.total_pieces() == 37

    # решетката е намерена от линиите, а не приета наум
    assert list(schedule.grid.column_bounds) == list(COLUMNS)
    assert schedule.grid.header_row_index >= 0
    assert schedule.grid.roles[0] == "position"
    # изходът е сериализуем — отчетите го записват както е
    json.dumps(schedule.to_dict(), ensure_ascii=False)


def test_u_shape_detail_is_ordered_through_the_bend_arcs():
    schedule = read_schedule(_straight_table().records)
    row = schedule.by_position()[2]

    assert row.detail_segment_count == 3
    assert row.detail_bend_count == 2
    assert row.detail_ordered is True
    assert row.detail_cm == (10.0, 300.0, 10.0)
    assert row.detail_total_cm == pytest.approx(row.length_cm)
    assert [l.segment_index for l in row.detail_labels] == [0, 1, 2]


# --------------------------------------------------------------------------- #
# 2) ред с марка, различна от „N“
# --------------------------------------------------------------------------- #
def test_row_with_diameter_symbol_instead_of_n_is_read():
    """ASD пише Ø чрез превключване на шрифта; в чист текст остава „0“.

    Старото четене търсеше точно „N“ след номера и такъв ред не се откриваше.
    """

    builder = _Builder(2)
    builder.row(0, 43, "N", 10, 80, 200)
    builder.straight_detail(0, "200")
    builder.row(1, 44, "0", 8, 135, 133)
    builder.straight_detail(1, "133")

    schedule = read_schedule(builder.records)

    assert [r.position for r in schedule.rows] == [43, 44]
    smooth = schedule.by_position()[44]
    assert smooth.mark_raw == "0"
    assert smooth.mark_kind == "Ø"
    assert smooth.diameter_mm == 8.0
    assert smooth.count == 135
    assert smooth.length_cm == 133.0
    assert smooth.detail_cm == (133.0,)
    assert any("Fdiam" in note for note in smooth.notes)


def test_unknown_mark_is_flagged_but_does_not_lose_the_row():
    builder = _Builder(1)
    builder.row(0, 7, "?", 12, 4, 500)
    builder.straight_detail(0, "500")

    schedule = read_schedule(builder.records)

    row = schedule.by_position()[7]
    assert row.mark_raw == "?"
    assert row.mark_kind == "НЕИЗВЕСТНА"
    assert row.count == 4
    assert any("непозната марка" in note for note in row.notes)


# --------------------------------------------------------------------------- #
# 3) „Детайл“ с разпръснати надписи
# --------------------------------------------------------------------------- #
def test_scattered_detail_labels_are_not_returned_as_ordered_segments():
    builder = _Builder(1)
    builder.row(0, 41, "N", 10, 1950, 88)
    builder.scattered_detail(0, ("20", "20", "14"))

    schedule = read_schedule(builder.records)
    row = schedule.by_position()[41]

    assert row.detail_segment_count == 5
    assert row.detail_ordered is False
    assert row.detail_cm == ()
    assert [l.text for l in row.detail_labels] == ["20", "20", "14"]
    assert all(l.segment_index is None for l in row.detail_labels)
    # сборът на надписите е наличен, но не се обявява за дължина
    assert row.detail_label_total_cm == 54.0
    assert row.detail_label_total_cm != row.length_cm
    assert "5" in row.detail_reason and "3" in row.detail_reason
    # надписите пазят мястото си, защото са на различни y
    assert len({round(l.y, 3) for l in row.detail_labels}) == 3


def test_detail_without_drawing_is_not_ordered():
    builder = _Builder(1)
    builder.row(0, 5, "N", 8, 3, 210)
    _, y = builder.centre(5, 0)
    builder.label("210", 700.0, y)

    row = read_schedule(builder.records).by_position()[5]

    assert row.detail_ordered is False
    assert row.detail_cm == ()
    assert "осева линия" in row.detail_reason


# --------------------------------------------------------------------------- #
# 4) клетките на един ред не изтичат в съседния
# --------------------------------------------------------------------------- #
def test_row_does_not_absorb_the_next_rows_cells(monkeypatch):
    """Редът се определя от решетката, а не от реда на документа."""

    builder = _Builder(3)
    builder.row(0, 43, "N", 10, 80, 200)
    builder.straight_detail(0, "200")
    builder.row(1, 44, "0", 8, 135, 133)
    builder.u_detail(1, ("20", "93", "20"))
    builder.row(2, 45, "N", 12, 8, 200)
    builder.straight_detail(2, "200")

    # редът на документа нарочно се разбърква: не бива да има значение
    shuffled = list(builder.records)
    shuffled.reverse()
    schedule = read_schedule(shuffled)

    assert [r.position for r in schedule.rows] == [43, 44, 45]
    assert schedule.unparsed == []
    upper = schedule.by_position()[43]
    middle = schedule.by_position()[44]
    lower = schedule.by_position()[45]

    # 200, не 200 + 20 + 93 + 20 = 333 (така изтичаше старото четене)
    assert upper.detail_cm == (200.0,)
    assert upper.detail_total_cm == 200.0
    assert middle.detail_cm == (20.0, 93.0, 20.0)
    assert lower.detail_cm == (200.0,)
    # всеки надпис е в лентата на своя ред
    for row in schedule.rows:
        low, high = row.band
        for label in row.detail_labels:
            assert low <= label.y <= high
        for cell in row.cells:
            assert low <= cell.y <= high


def test_two_values_in_one_column_are_unparsed_not_guessed():
    builder = _Builder(1)
    builder.row(0, 11, "N", 8, 6, 400)
    builder.straight_detail(0, "400")
    # втора стойност в колоната „брой“ — редът не се чете
    x, y = builder.centre(3, 0)
    builder.cell("6", 3, 0)

    schedule = read_schedule(builder.records)

    assert schedule.rows == []
    assert len(schedule.unparsed) == 1
    unparsed = schedule.unparsed[0]
    assert "count" in unparsed.reason
    assert "2 стойности" in unparsed.reason
    assert any(cell.column_role == "count" for cell in unparsed.cells)


def test_non_numeric_length_is_unparsed():
    builder = _Builder(1)
    builder.row(0, 12, "N", 8, 6, "?")
    builder.straight_detail(0, "400")

    schedule = read_schedule(builder.records)

    assert schedule.rows == []
    assert len(schedule.unparsed) == 1
    assert "дължината" in schedule.unparsed[0].reason


# --------------------------------------------------------------------------- #
# 5) празна или липсваща ведомост
# --------------------------------------------------------------------------- #
def test_absent_schedule_says_nothing():
    assert has_schedule([]) is False
    schedule = read_schedule([])
    assert schedule.rows == []
    assert schedule.unparsed == []
    assert schedule.header_seen is False
    assert schedule.total_pieces() == 0
    assert schedule.to_dict()["row_count"] == 0


def test_other_layers_are_ignored():
    other = DxfEntityRecord(
        handle="AAA",
        entity_type="MTEXT",
        layer="AutoCAD_Structural_Detailing_Distribution description",
        geometry={"insert": [10.0, 10.0, 0.0], "attachment_point": 5},
        text=" 21N8x1200/20",
    )
    assert has_schedule([other]) is False
    assert read_schedule([other]).rows == []


def test_layer_without_grid_refuses_instead_of_guessing():
    records = [
        DxfEntityRecord(
            handle="B1",
            entity_type="MTEXT",
            layer=LAYER,
            geometry={"insert": [0.0, 0.0, 0.0], "attachment_point": 5},
            text="1",
        ),
        DxfEntityRecord(
            handle="B2",
            entity_type="MTEXT",
            layer=LAYER,
            geometry={"insert": [50.0, 0.0, 0.0], "attachment_point": 5},
            text="N",
        ),
    ]
    schedule = read_schedule(records)

    assert has_schedule(records) is True
    assert schedule.rows == []
    assert schedule.header_seen is False
    assert schedule.skipped_cells == 2
    assert any("решетка" in note for note in schedule.notes)


def test_grid_without_headers_refuses():
    builder = _Builder(1)
    # изтриваме заглавните клетки: без тях не е ясно кой стълб какво е
    builder.records = [
        record
        for record in builder.records
        if (record.text or "") not in HEADERS
    ]
    builder.row(0, 1, "N", 8, 10, 300)

    schedule = read_schedule(builder.records)

    assert schedule.header_seen is False
    assert schedule.rows == []
    assert any("Заглавията" in note for note in schedule.notes)


def test_empty_cells_do_not_create_rows():
    builder = _Builder(2)
    builder.row(0, 1, "N", 8, 10, 300)
    builder.straight_detail(0, "300")
    builder.cell("", 0, 1)

    schedule = read_schedule(builder.records)

    assert [r.position for r in schedule.rows] == [1]
    assert schedule.unparsed == []


# --------------------------------------------------------------------------- #
# Сверяване по полета
# --------------------------------------------------------------------------- #
def _program_row(**kwargs):
    row = {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": "P1",
        "annotation_text": "10N8x300",
        "annotation_count": 10,
        "diameter_mm": 8.0,
        "declared_length_cm": 300.0,
        "master_shape_code": "F001",
        "segment_count": 1,
        "canonical_segment_lengths_cm": [],
    }
    row.update(kwargs)
    return row


def test_compare_with_rows_keeps_its_name_and_reports_what_it_compared():
    schedule = read_schedule(_straight_table().records)
    program = [
        _program_row(),
        _program_row(
            annotation_handle="P2",
            annotation_text="18N10x320",
            annotation_count=18,
            diameter_mm=10.0,
            declared_length_cm=320.0,
            master_shape_code="F003",
            segment_count=3,
            canonical_segment_lengths_cm=[10.0, 300.0, 10.0],
        ),
    ]

    result = compare_with_rows(schedule, program)

    # старите ключове остават достъпни за водещия
    for key in (
        "schema",
        "schedule_rows",
        "schedule_pieces",
        "matched",
        "different",
        "only_program",
        "only_schedule",
        "rows",
    ):
        assert key in result
    assert result["schedule_pieces"] == 37
    assert result["compared_fields"] == ["count"]
    verdicts = {row["verdict"] for row in result["rows"]}
    # никъде голо „СЪВПАДА“: присъдата казва какво точно е сравнено
    assert "СЪВПАДА" not in verdicts
    assert "БРОЯТ СЪВПАДА" in verdicts
    assert all(row["compared_fields"] == ["count"] for row in result["rows"])
    assert "per_field" in result


def test_per_field_comparison_separates_the_five_fields():
    schedule = read_schedule(_straight_table().records)
    program = [
        _program_row(),  # брой съвпада, форма съвпада
        _program_row(
            annotation_handle="P2",
            annotation_text="18N10x320",
            annotation_count=18,  # различен брой
            diameter_mm=10.0,
            declared_length_cm=320.0,
            master_shape_code="F004",
            segment_count=2,  # различна форма
            canonical_segment_lengths_cm=[20.0, 300.0],  # различни участъци
        ),
        _program_row(
            annotation_handle="P3",
            annotation_text="4N16x900",
            annotation_count=4,
            diameter_mm=16.0,  # диаметър само в програмата
            declared_length_cm=900.0,
        ),
        _program_row(
            annotation_handle="P4",
            annotation_text="1950N10",
            annotation_class="MISSING_LENGTH",
            declared_length_cm=None,
            annotation_count=1950,
        ),
    ]

    result = compare_per_field(schedule, program)
    fields = result["fields"]
    assert set(fields) == {"count", "diameter", "length", "shape", "segments"}

    by_key = {
        (row["diameter_mm"], row["length_cm"]): row for row in fields["count"]["rows"]
    }
    assert by_key[(8.0, 300.0)]["verdict"] == "БРОЯТ СЪВПАДА"
    assert by_key[(10.0, 320.0)]["verdict"] == "РАЗЛИЧЕН БРОЙ"
    assert by_key[(10.0, 320.0)]["difference"] == -2
    assert by_key[(12.0, 450.0)]["verdict"] == "САМО ВЪВ ВЕДОМОСТТА"
    assert by_key[(16.0, 900.0)]["verdict"] == "САМО В ПРОГРАМАТА"

    shapes = {
        (row["diameter_mm"], row["length_cm"]): row for row in fields["shape"]["rows"]
    }
    assert shapes[(8.0, 300.0)]["verdict"] == "БРОЯТ УЧАСТЪЦИ СЪВПАДА"
    assert shapes[(10.0, 320.0)]["verdict"] == "РАЗЛИЧНА ФОРМА"
    assert shapes[(10.0, 320.0)]["schedule_drawn_segments"] == [3]
    assert shapes[(10.0, 320.0)]["program_segment_counts"] == [2]

    segments = {
        (row["diameter_mm"], row["length_cm"]): row
        for row in fields["segments"]["rows"]
    }
    assert segments[(10.0, 320.0)]["verdict"] == "РАЗЛИЧНИ УЧАСТЪЦИ"
    assert segments[(10.0, 320.0)]["schedule_segments"] == [[10.0, 300.0, 10.0]]
    assert segments[(8.0, 300.0)]["verdict"] == "НЕСРАВНИМО"

    diameters = {row["diameter_mm"]: row for row in fields["diameter"]["rows"]}
    assert diameters[16.0]["verdict"] == "САМО В ПРОГРАМАТА"
    assert diameters[8.0]["verdict"] == "И В ДВЕТЕ"

    # отпадналите позиции на програмата пътуват с причината си
    excluded = {entry["annotation_handle"] for entry in result["program_excluded"]}
    assert excluded == {"P4"}


def test_per_field_says_why_a_detail_is_not_comparable():
    builder = _Builder(1)
    builder.row(0, 41, "N", 10, 1950, 88)
    builder.scattered_detail(0, ("20", "20", "14"))
    schedule = read_schedule(builder.records)

    result = compare_per_field(
        schedule,
        [
            _program_row(
                annotation_handle="P9",
                annotation_text="1950N10x88",
                annotation_count=1950,
                diameter_mm=10.0,
                declared_length_cm=88.0,
                master_shape_code="F004",
                segment_count=2,
                canonical_segment_lengths_cm=[47.0, 41.0],
            )
        ],
    )

    segments = result["fields"]["segments"]["rows"][0]
    assert segments["verdict"] == "НЕСРАВНИМО"
    assert segments["schedule_unordered"][0]["position"] == 41
    assert segments["schedule_unordered"][0]["drawn_segments"] == 5
    assert "надписа" in segments["schedule_unordered"][0]["reason"]

    shape = result["fields"]["shape"]["rows"][0]
    assert shape["verdict"] == "РАЗЛИЧНА ФОРМА"


def test_compare_with_empty_schedule_reports_only_the_program():
    result = compare_with_rows(Schedule(), [_program_row()])

    assert result["schedule_rows"] == 0
    assert result["only_program"] == 1
    assert result["rows"][0]["verdict"] == "САМО В ПРОГРАМАТА"
    assert result["per_field"]["fields"]["count"]["tally"] == {"САМО В ПРОГРАМАТА": 1}
