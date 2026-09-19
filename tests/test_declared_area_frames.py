"""Обявена рамка: правоъгълник на слой, чието име се класифицира (rc44).

Собственик, 2026-09-19, върху първия чертеж от друг проектант (AutoCAD
Structural Detailing, `Тест-желязо\\d-2`): „ще ти направя 4 рамки с номера
1 долна скара, 2 долна усилване, 3 горна скара, 4 горна усилване“.

ЗАЩО. Този чертеж няма щемпел и няма нито един текст с „АРМИРОВ“ или
„ЧЕРТЕЖ“ — заглавието му е само в името на файла. Затова рамки не се
намираха, портата отказваше всяка позиция с ``MISSING_FRAME`` и от 178
позиции се одобряваха **0**, макар анализът да даваше 124 силни.

Един лист държи четири плана — основна скара и усилване, долу и горе.
Затова обявената рамка носи граници и по y: лентата по x не дели планове,
които стоят един над друг.
"""
from __future__ import annotations

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.frame_detection import (
    assign_point_to_frame,
    detect_drawing_frames,
)

#: Четирите плана от `d-2`, смалени и преместени към началото.
_ПЛАНОВЕ = (
    ("Долна-Скара", (0.0, 0.0, 500.0, 300.0)),
    ("Горна-Скара", (520.0, 0.0, 1000.0, 300.0)),
    ("Долна-Усилване", (0.0, 340.0, 500.0, 600.0)),
    ("Горна-Усилване", (520.0, 340.0, 1000.0, 600.0)),
)


def _рамка(layer: str, box, handle: str = "") -> DxfEntityRecord:
    x0, y0, x1, y1 = box
    return DxfEntityRecord(
        handle=handle or f"R-{layer}",
        entity_type="LWPOLYLINE",
        layer=layer,
        geometry={
            "points": [
                [x0, y0, 0.0],
                [x1, y0, 0.0],
                [x1, y1, 0.0],
                [x0, y1, 0.0],
                [x0, y0, 0.0],
            ]
        },
    )


def _текст(handle: str, layer: str, стойност: str, x: float, y: float):
    return DxfEntityRecord(
        handle=handle,
        entity_type="TEXT",
        layer=layer,
        text=стойност,
        geometry={"insert": [x, y, 0.0], "height": 10.0},
    )


def _чертеж(*, допълнително=()) -> list[DxfEntityRecord]:
    records = [_рамка(layer, box) for layer, box in _ПЛАНОВЕ]
    # Таблицата също е оградена, но името ѝ не е категория.
    records.append(_рамка("Подробна спецификация", (-400.0, 0.0, -100.0, 300.0)))
    records.extend(допълнително)
    return records


class TestОбявенаРамка:
    def test_четирите_плана_дават_четири_листа(self) -> None:
        frames = detect_drawing_frames(_чертеж())

        assert [f.category_code for f in frames] == [
            "BOTTOM",
            "BOTTOM_EXTRA",
            "TOP",
            "TOP_EXTRA",
        ]
        assert [f.suggested_sheet_name for f in frames] == [
            "долна",
            "долна усилване",
            "горна",
            "горна усилване",
        ]
        assert {f.resolution_reason for f in frames} == {"DECLARED_AREA_LAYER"}
        assert {f.resolved_by for f in frames} == {"OPERATOR"}

    def test_неклассифициращият_се_слой_се_пропуска(self) -> None:
        """„Подробна спецификация“ огражда таблицата, не лист с армировка."""

        frames = detect_drawing_frames(_чертеж())

        assert "Подробна спецификация" not in {f.title_text for f in frames}

    def test_позицията_отива_в_своя_план(self) -> None:
        """Лентата по x не дели плановете — те стоят един над друг."""

        frames = list(detect_drawing_frames(_чертеж()))
        по_код = {f.category_code: f.index for f in frames}

        assert assign_point_to_frame(250.0, 150.0, frames) == по_код["BOTTOM"]
        assert assign_point_to_frame(250.0, 450.0, frames) == по_код["BOTTOM_EXTRA"]
        assert assign_point_to_frame(760.0, 150.0, frames) == по_код["TOP"]
        assert assign_point_to_frame(760.0, 450.0, frames) == по_код["TOP_EXTRA"]

    def test_точка_извън_плановете_няма_лист(self) -> None:
        """Таблицата и легендата не са позиции на лист."""

        frames = list(detect_drawing_frames(_чертеж()))

        assert assign_point_to_frame(-250.0, 150.0, frames) is None
        assert assign_point_to_frame(250.0, 320.0, frames) is None


class TestОтказите:
    def test_два_правоъгълника_на_един_слой_не_обявяват_нищо(self) -> None:
        допълнителен = _рамка("Долна-Скара", (0.0, 700.0, 500.0, 900.0), handle="R2")
        frames = detect_drawing_frames(_чертеж(допълнително=[допълнителен]))

        assert "Долна-Скара" not in {f.title_text for f in frames}
        assert {f.category_code for f in frames} == {"BOTTOM_EXTRA", "TOP", "TOP_EXTRA"}

    def test_застъпени_обявени_рамки_са_отказ(self) -> None:
        """Не се знае чия е позицията в застъпването — нито една не важи."""

        застъпена = _рамка("Детайли армировка", (400.0, 100.0, 900.0, 500.0))
        frames = detect_drawing_frames(_чертеж(допълнително=[застъпена]))

        assert frames == []


class TestНеПречи:
    def test_щемпелът_бие_обявената_рамка(self) -> None:
        """Чертеж с „ЧЕРТЕЖ:“ запазва поведението си непроменено."""

        щемпел = [
            _текст("M1", "$Ramka", "ЧЕРТЕЖ:", 2000.0, 2000.0),
            _текст("T1", "$Ramka", "ДОЛНА АРМИРОВКА НА КОТА +5.65", 2100.0, 2000.0),
        ]
        frames = detect_drawing_frames(_чертеж(допълнително=щемпел))

        assert len(frames) == 1
        assert frames[0].marker_handle == "M1"
        assert frames[0].resolution_reason != "DECLARED_AREA_LAYER"
