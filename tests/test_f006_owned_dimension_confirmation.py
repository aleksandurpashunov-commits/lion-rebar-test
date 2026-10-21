# -*- coding: utf-8 -*-
"""Второто доказателство за стреме с куки — собствените коти на контура.

Случаят, от който излиза това: `d-1`, надпис `4819::BJ-FI-L` = `25N8x395`,
кандидат `4800` (LWPOLYLINE, слой `Stremena`, 12 върха). Котите `4801` = 165,0127,
`4802` = 20,0000 и `4803` = 10,0000 са с опорни точки върху върховете на самия
контур (0,0000–0,0103). Редът твърдеше `f006_printed_dimensions_missing = True`,
докато `production_segments_cm` вече беше `[10, 20, 165, 20, 165, 10]` — тоест
измерената геометрия и котите на проектанта казват едно и също, но второто
доказателство не се четеше.

Числата в тестовете са от този случай. Той е ПРИМЕР, не изключение в кода:
никъде няма handle, име на файл или конкретен размер.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf.f006_owned_dimension_confirmation import (
    БЕЗ_СОБСТВЕНИ_КОТИ,
    НЕЗАТВОРЕНО_УРАВНЕНИЕ,
    ПОЛЕ,
    ПОТВЪРДЕНО,
    РАЗМИНАВАНЕ,
    confirm_row,
    hooked_closure_is_confirmed,
)
from lion_rebar.dxf.vertical_shape_resolver import read_owned_stirrup
from lion_rebar.engine.bend_allowance import segments_match_total


# --- помощни ---------------------------------------------------------------

СЕГМЕНТИ = [10.0, 20.0, 165.0, 20.0, 165.0, 10.0]   # сборът е 390 при обявени 395


def _ред(**промени):
    ред = {
        "master_shape_code": "F006",
        "declared_length_cm": 395.0,
        "production_segments_cm": list(СЕГМЕНТИ),
        "candidate_handles": ["4800"],
        "review_reason_codes": ["HOOKED_CLOSURE"],
    }
    ред.update(промени)
    return ред


def _присъда(ред, стойности=None, собственик="4800", кука=10.0, записи=None):
    стойности = [165.0127, 20.0, 10.0] if стойности is None else стойности
    свои = {собственик: list(стойности)} if стойности else {}
    записи = записи if записи is not None else [
        {"handle": "4801", "measurement": 165.0127},
        {"handle": "4802", "measurement": 20.0},
        {"handle": "4803", "measurement": 10.0},
    ]
    return confirm_row(
        ред,
        owned_values_by_contour=свои,
        owned_records_by_contour={собственик: записи},
        preferred_hook=кука,
        read_owned_stirrup=read_owned_stirrup,
        segments_match_total=segments_match_total,
    )


# --- потвърждението --------------------------------------------------------


def test_sobstvenite_koti_potvurzhdavat_izmerenata_geometriya() -> None:
    """Двата независими източника дават един и същ производствен вектор."""

    ред = _ред()
    присъда = _присъда(ред)
    assert присъда["status"] == ПОТВЪРДЕНО
    assert присъда["owner_geometry_handle"] == "4800"
    assert присъда["dimension_handles"] == ["4801", "4802", "4803"]
    assert присъда["segments_from_owned_dimensions_cm"] == pytest.approx(
        СЕГМЕНТИ, abs=0.02)
    # присъдата носи и двата вектора, за да е проверимо от какво идва
    assert присъда["segments_in_row_cm"] == СЕГМЕНТИ
    assert присъда["declared_length_cm"] == 395.0


def test_potvurzhdenieto_NE_meni_nito_edno_chislo() -> None:
    """Инвариантът: проходът добавя доказателство, не производствени размери."""

    ред = _ред()
    преди = dict(ред)
    _присъда(ред)
    assert ред == преди, "confirm_row не бива да пише в реда"


def test_kvadratno_streme_s_dve_koti_se_potvurzhdava() -> None:
    """Две коти са квадратно стреме — допускане за формата, не досмятане."""

    сегменти = [10.0, 95.0, 95.0, 95.0, 95.0, 10.0]
    ред = _ред(production_segments_cm=list(сегменти), declared_length_cm=405.0)
    присъда = _присъда(ред, стойности=[95.0, 10.0], записи=[
        {"handle": "A1", "measurement": 95.0},
        {"handle": "A2", "measurement": 10.0},
    ])
    assert присъда["status"] == ПОТВЪРДЕНО


# --- отказите --------------------------------------------------------------


def test_koti_ot_dva_kontura_NE_se_obedinyavat() -> None:
    """Два кандидата със собствени коти са отказ, не сбор от съседи."""

    ред = _ред(candidate_handles=["4800", "4900"])
    присъда = confirm_row(
        ред,
        owned_values_by_contour={"4800": [165.0, 20.0], "4900": [10.0]},
        owned_records_by_contour={},
        preferred_hook=10.0,
        read_owned_stirrup=read_owned_stirrup,
        segments_match_total=segments_match_total,
    )
    assert присъда["status"] == БЕЗ_СОБСТВЕНИ_КОТИ
    assert "точно един кандидат" in присъда["reason"]


def test_edna_kota_ne_se_dosmyata_do_tri() -> None:
    """Един размер НЕ се допълва от разликата с обявената дължина."""

    ред = _ред()
    присъда = _присъда(ред, стойности=[165.0])
    assert присъда["status"] == БЕЗ_СОБСТВЕНИ_КОТИ
    assert "НЕ се досмята" in присъда["reason"]


def test_nezatvoreno_uravnenie_e_otkaz() -> None:
    """Сборът на котите не се затваря с обявената дължина — отказ."""

    ред = _ред(declared_length_cm=700.0)
    присъда = _присъда(ред)
    assert присъда["status"] == НЕЗАТВОРЕНО_УРАВНЕНИЕ


def test_druga_naredba_e_sushtoto_streme_no_se_otbelyazva() -> None:
    """Затворено стреме 95 x 20 и 20 x 95 е един и същ обект.

    Измерено на тест-004, 3 позиции (например 5C10::BJ-FI-L = 28N8x255):
    котите дават [10, 20, 95, 20, 95, 10], а измерената геометрия
    [10, 95, 20, 95, 20, 10] - същият правоъгълник, обходен от другата страна.
    Потвърждава се, но разликата в наредбата се записва: за машинния изход
    посоката значи нещо. НЕ се поправя тук - това би сменило число.
    """

    ред = _ред(production_segments_cm=[10.0, 165.0, 20.0, 165.0, 20.0, 10.0])
    присъда = _присъда(ред)
    assert присъда["status"] == ПОТВЪРДЕНО
    assert присъда["order_differs"] is True
    assert "РАЗЛИЧНА НАРЕДБА" in присъда["reason"]


def test_sushtata_naredba_ne_se_otbelyazva() -> None:
    присъда = _присъда(_ред())
    assert присъда["status"] == ПОТВЪРДЕНО
    assert присъда["order_differs"] is False


def test_bezsmislena_naredba_sus_sushtite_stoynosti_e_razminavane() -> None:
    """Същите стойности, но устройството не е на стреме."""

    ред = _ред(production_segments_cm=[10.0, 20.0, 20.0, 165.0, 165.0, 10.0])
    assert _присъда(ред)["status"] == РАЗМИНАВАНЕ


def test_razminavane_mezhdu_dvata_iztochnika_NE_izbira_pobeditel() -> None:
    """Различен размерен комплект: записва се противоречието, не решение."""

    ред = _ред(production_segments_cm=[10.0, 30.0, 155.0, 30.0, 155.0, 10.0])
    присъда = _присъда(ред)
    assert присъда["status"] == РАЗМИНАВАНЕ
    # и двата вектора са в присъдата, за да види операторът какво се разминава
    assert присъда["segments_from_owned_dimensions_cm"] == pytest.approx(
        СЕГМЕНТИ, abs=0.02)
    assert присъда["segments_in_row_cm"][1] == 30.0
    assert присъда.get("order_differs") is None  # при разминаване не се пита
    assert not hooked_closure_is_confirmed({ПОЛЕ: присъда})


def test_bez_dokazan_vektor_sluchayat_ne_e_za_tozi_modul() -> None:
    """Без вече доказани сегменти работи пътят, който ИЗВЕЖДА размерите."""

    assert _присъда(_ред(production_segments_cm=[])) is None
    assert _присъда(_ред(production_segments_cm=[10.0, 20.0])) is None


def test_druga_forma_ne_se_pipa() -> None:
    assert _присъда(_ред(master_shape_code="F003")) is None
    assert _присъда(_ред(master_shape_code="")) is None


# --- пазачът за matcher-а ---------------------------------------------------


@pytest.mark.parametrize("състояние,очаквано", [
    (ПОТВЪРДЕНО, True),
    (РАЗМИНАВАНЕ, False),
    (БЕЗ_СОБСТВЕНИ_КОТИ, False),
    (НЕЗАТВОРЕНО_УРАВНЕНИЕ, False),
])
def test_samo_potvurdenoto_vazhi(състояние, очаквано) -> None:
    assert hooked_closure_is_confirmed({ПОЛЕ: {"status": състояние}}) is очаквано


def test_lipsvashto_pole_znachi_ne() -> None:
    assert hooked_closure_is_confirmed({}) is False
    assert hooked_closure_is_confirmed({ПОЛЕ: None}) is False
    assert hooked_closure_is_confirmed({ПОЛЕ: "потвърдено"}) is False
