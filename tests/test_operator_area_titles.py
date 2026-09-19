"""Кой текст е заглавие на област и кога рамката отказва (rc44).

Рамката с два клика трябва да работи еднакво за греди, шайби и колони, но
трите чертежа именуват по различен начин. Тук се проверява, че всеки от
трите се разпознава и че fail-closed правилото важи и за трите: нула или две
заглавия в рамката е отказ, не догадка.

Образците не са измислени. Проверени са върху истинските файлове: в ``s-7``
строгият образец за шайби хваща 23 текста, всички на слой ``C_TXT_shaibi``,
а разхлабването му до „Ш<число>" би хванало още 49 котировки на слоеве
``_text`` и ``KOTA-TXT`` („Ш10", „Ш3"), които НЕ са заглавия.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.frame_detection import detect_drawing_frames
from lion_rebar.export.operator_areas import KIND_BEAM, KIND_GROUP
from lion_rebar.export.operator_group_box import (
    OperatorBoxError,
    collect_area_titles,
    resolve_operator_area,
)


def _beam_sheet() -> list[DxfEntityRecord]:
    """Чертежът на гредите: маркер „ЧЕРТЕЖ:" плюс заглавие вдясно."""

    return [
        DxfEntityRecord("MARKER", "TEXT", "$Ramka", {"insert": [113000.0, 5000.0, 0.0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord(
            "FRAME", "TEXT", "$Ramka", {"insert": [113104.0, 5000.0, 0.0]},
            "АРМИРОВКА ГРЕДИ НА КОТА +5.90 ; +8.90 и +11.90 (B)",
        ),
        DxfEntityRecord(
            "BL19", "TEXT", "_text",
            {"insert": [113837.0, 2678.0, 0.0], "height": 35.0}, "Гр. B.19 25/55",
        ),
        DxfEntityRecord(
            "BL20", "TEXT", "_text",
            {"insert": [114559.0, 2678.0, 0.0], "height": 35.0}, "Гр. B.20 25/40",
        ),
    ]


def _wall_sheet() -> list[DxfEntityRecord]:
    """Чертежът на шайбите: един лист, разпознат по заглавие плюс INSERT."""

    return [
        DxfEntityRecord("RAMKA", "INSERT", "ramka_1", {"insert": [0.0, -145000.0, 0.0]}, ""),
        DxfEntityRecord(
            "FRAME", "TEXT", "$Ramka", {"insert": [6104.0, -138000.0, 0.0]},
            "АРМИРОВКА ШАЙБИ ОТ КОТА +18,18 ДО КОТА +21,48",
        ),
        DxfEntityRecord(
            "SH3", "TEXT", "C_TXT_shaibi",
            {"insert": [6845.0, -139420.0, 0.0], "height": 30.0}, "Ш 3-25/600",
        ),
        DxfEntityRecord(
            "SH11", "TEXT", "C_TXT_shaibi",
            {"insert": [6716.0, -141154.0, 0.0], "height": 30.0}, "Ш 11 - 145/25",
        ),
        # Котировките, които НЕ са заглавия -- истински текстове от ``s-7``.
        DxfEntityRecord("K1", "TEXT", "KOTA-TXT", {"insert": [6800.0, -139500.0, 0.0]}, "Ш3"),
        DxfEntityRecord("K2", "TEXT", "_text", {"insert": [6810.0, -139510.0, 0.0]}, "Ш10"),
    ]


def _titles(entities):
    return collect_area_titles(entities, detect_drawing_frames(entities))


class TestРазпознаване:
    def test_гредата_е_заглавие_от_вид_греда(self) -> None:
        titles = _titles(_beam_sheet())

        assert {(t.text, t.kind) for t in titles} == {
            ("Гр. B.19 25/55", KIND_BEAM),
            ("Гр. B.20 25/40", KIND_BEAM),
        }

    def test_шайбата_е_заглавие_от_вид_група(self) -> None:
        titles = _titles(_wall_sheet())

        assert {(t.text, t.kind) for t in titles} == {
            ("Ш 3-25/600", KIND_GROUP),
            ("Ш 11 - 145/25", KIND_GROUP),
        }

    def test_котировката_не_е_заглавие(self) -> None:
        """„Ш3" и „Ш10" са размерни надписи, не имена на шайби.

        Ако образецът се разхлаби до „Ш<число>", всяка рамка ще намира по
        няколко „заглавия" и ще отказва — тоест функцията става безполезна
        точно там, където трябва да работи.
        """

        texts = {t.text for t in _titles(_wall_sheet())}

        assert "Ш3" not in texts
        assert "Ш10" not in texts

    def test_колонната_група_е_заглавие_от_вид_група(self) -> None:
        entities = [
            DxfEntityRecord("MARKER", "TEXT", "$Ramka", {"insert": [0.0, 5000.0, 0.0]}, "ЧЕРТЕЖ:"),
            DxfEntityRecord(
                "FRAME", "TEXT", "$Ramka", {"insert": [104.0, 5000.0, 0.0]},
                "АРМИРОВКА КОЛОНИ ОТ КОТА +18,18 ДО КОТА +21,48",
            ),
            DxfEntityRecord(
                "H1", "TEXT", "arm_koloni", {"insert": [500.0, 3000.0, 0.0]},
                "К23, К24 (35/35) - 2бр.",
            ),
        ]

        titles = _titles(entities)

        assert [(t.text, t.kind) for t in titles] == [
            ("К23, К24 (35/35) - 2бр.", KIND_GROUP)
        ]


class TestРазчитане:
    def test_рамка_около_една_греда(self) -> None:
        titles = _titles(_beam_sheet())

        res = resolve_operator_area((113300.0, 2600.0), (113900.0, 2950.0), titles=titles)

        assert res.title_text == "Гр. B.19 25/55"
        assert res.kind == KIND_BEAM
        assert res.box == (113300.0, 2600.0, 113900.0, 2950.0)

    def test_посоката_на_влаченето_няма_значение(self) -> None:
        titles = _titles(_beam_sheet())

        обратно = resolve_operator_area((113900.0, 2950.0), (113300.0, 2600.0), titles=titles)

        assert обратно.title_text == "Гр. B.19 25/55"

    def test_рамка_около_една_шайба(self) -> None:
        titles = _titles(_wall_sheet())

        res = resolve_operator_area((6445.0, -139620.0), (7245.0, -139360.0), titles=titles)

        assert res.title_text == "Ш 3-25/600"
        assert res.kind == KIND_GROUP

    def test_празна_рамка_е_отказ(self) -> None:
        titles = _titles(_beam_sheet())

        with pytest.raises(OperatorBoxError, match="няма заглавие"):
            resolve_operator_area((0.0, 0.0), (100.0, 100.0), titles=titles)

    def test_две_заглавия_са_отказ_не_догадка(self) -> None:
        titles = _titles(_beam_sheet())

        with pytest.raises(OperatorBoxError, match="2 заглавия"):
            resolve_operator_area((113300.0, 2600.0), (114700.0, 2950.0), titles=titles)

    def test_рамка_с_нулева_височина_е_отказ(self) -> None:
        titles = _titles(_beam_sheet())

        with pytest.raises(OperatorBoxError, match="нулева"):
            resolve_operator_area((113300.0, 2678.0), (113900.0, 2678.0), titles=titles)


# ---------------------------------------------------------------------------
# Заглавие, познато по профила на доказаните в същия чертеж
# ---------------------------------------------------------------------------


def _текст(handle: str, стойност: str, x: float, y: float, height: float = 35.0,
           layer: str = "_text") -> DxfEntityRecord:
    return DxfEntityRecord(
        handle, "TEXT", layer, {"insert": [x, y, 0.0], "height": height}, стойност
    )


def _лист_с_греди(*, брой: int = 3, допълнително=()) -> list[DxfEntityRecord]:
    """Чертеж на греди плюс каквото се подава — за профила на заглавията."""

    records = _beam_sheet()[:2]  # само щемпелът и заглавието на чертежа
    for index in range(брой):
        records.append(
            _текст(f"BL{index}", f"Гр. B.{index + 1} 25/55", 113837.0 + index * 700.0, 2678.0)
        )
    records.extend(допълнително)
    return records


class TestЗаглавиеПоПрофил:
    """Собственик, 2026-09-19, върху „Плоча кота 14.90“.

    Рамката отказваше „Щурцове 1 до 9“, „Б-B.2 капандури“ и „Борд -A.1 върху
    плоча“ — реални елементи с армировка, които операторът трябва да огради.
    Речникът познаваше само греда, вертикална секция, шайба и колонна група.

    Чертежът обаче сам казва кое е заглавие: и 17-те стоят на един слой, с
    една височина и един цвят, а 14 от тях вече са разпознати като греди.
    """

    def test_непознато_заглавие_със_същия_профил_се_приема(self) -> None:
        titles = _titles(
            _лист_с_греди(допълнително=[_текст("SHT", "Щурцове 1 до 9", 115500.0, 2678.0)])
        )

        по_текст = {t.text: t.kind for t in titles}
        assert по_текст.get("Щурцове 1 до 9") == KIND_GROUP
        assert по_текст.get("Гр. B.1 25/55") == KIND_BEAM

    def test_друг_профил_не_е_заглавие(self) -> None:
        """Котировките и бележките са с друга височина — те не са заглавия."""

        titles = _titles(
            _лист_с_греди(
                допълнително=[
                    _текст("NOTE", "Снаждане минимум 60 см !!!", 115500.0, 2678.0, height=12.0),
                    _текст("OTHER", "Щурцове 1 до 9", 116500.0, 2678.0, layer="koti"),
                ]
            )
        )

        текстове = {t.text for t in titles}
        assert "Снаждане минимум 60 см !!!" not in текстове
        assert "Щурцове 1 до 9" not in текстове

    def test_под_три_доказани_заглавия_профилът_не_важи(self) -> None:
        """Две съвпадения може да са случайност — профилът иска доказателство."""

        titles = _titles(
            _лист_с_греди(брой=2, допълнително=[_текст("SHT", "Щурцове 1 до 9", 115500.0, 2678.0)])
        )

        assert "Щурцове 1 до 9" not in {t.text for t in titles}

    def test_два_различни_профила_сред_доказаните_са_отказ(self) -> None:
        """Няма един стил — няма и правило."""

        records = _лист_с_греди(брой=3)
        records[-1] = _текст("BL2", "Гр. B.3 25/55", 115237.0, 2678.0, height=20.0)
        records.append(_текст("SHT", "Щурцове 1 до 9", 115500.0, 2678.0))

        assert "Щурцове 1 до 9" not in {t.text for t in _titles(records)}

    def test_вторият_ред_е_продължение_не_конкурент(self) -> None:
        """„Борд -A.1 върху плоча“ / „H=84cm, L=231m“ са едно заглавие."""

        titles = _titles(
            _лист_с_греди(
                допълнително=[
                    _текст("BORD", "Борд -A.1 върху плоча", 115500.0, 2678.0),
                    _текст("BORD2", "H=84cm, L=231m", 115520.0, 2638.0),
                ]
            )
        )

        текстове = {t.text for t in titles}
        assert "Борд -A.1 върху плоча" in текстове
        assert "H=84cm, L=231m" not in текстове
