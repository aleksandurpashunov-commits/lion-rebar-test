"""Заявима добавка за огъване: инженерът пише число и то трябва да ЗАТВОРИ.

ЗАЩО. Измерените правила (таваните на F006/F009 и закръглянето нагоре до 5 cm)
обясняват почти всичко. Отделни начертани детайли обаче не се обясняват от тях:
при F007 „S за продънване“ отпечатаните коти дават 44 при надписана дължина 47,
а закръглянето сочи 45. ``segments_match_total`` отказва и е ПРАВ да отказва —
ново общо правило по един детайл би било догадка, още повече че 21 от 334
подменени коти в корпуса сочат в обратната посока (``PROJECT_HANDOFF.md``, § 6н).

Затова числото се ЗАЯВЯВА за една позиция и се проверява: сбор + заявено = L.
Не е таван („до 3“), а равенство — сгрешено число не минава.

Тестовете пазят три неща:

1. заявеното число работи точно когато затваря;
2. без заявка НИЩО не се променя — закръглянето до 5 остава правилото;
3. числото пътува от решението до експорта, защото проверката там се прави
   наново и иначе позицията би паднала СЛЕД одобрението.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lion_rebar.engine.bend_allowance import (
    declared_allowance_closes,
    explain_mismatch,
    segments_match_total,
)
from lion_rebar.engineer_decision import (
    DECLARED_BEND_ALLOWANCE_MAX_CM,
    declared_bend_allowance,
    save_engineer_decision,
)
from lion_rebar.export.excel_export import _declared_bend_allowance_of_link
from lion_rebar.export.master_engine_v031 import (
    MasterDimensionError,
    parameters_from_segments,
)

# Измереният случай: F007 „S за продънване“, 10 + 21 + 13 = 44 при L = 47.
# Крайните рамена на F007 трябва да са еднакви, затова синтетичният ред ползва
# 13 + 18 + 13 = 44 — същият недостиг от 3 cm, същата форма.
СБОР = 44.0
ДЪЛЖИНА = 47.0
ДОБАВКА = 3.0


# --------------------------------------------------------------------------- #
# 1. правилото


def test_zayavenoto_chislo_zatvarya_uravnenieto() -> None:
    assert not segments_match_total("F007", СБОР, ДЪЛЖИНА)
    assert segments_match_total(
        "F007", СБОР, ДЪЛЖИНА, declared_allowance_cm=ДОБАВКА
    )


@pytest.mark.parametrize("число", [5.0, 2.0, 30.0, 0.0, -3.0, None, "три"])
def test_pogreshno_zayaveno_chislo_ne_minava(число) -> None:
    """Числото не се ПОГАЖДА: приема се само това, което затваря."""

    assert not segments_match_total(
        "F007", СБОР, ДЪЛЖИНА, declared_allowance_cm=число
    )


def test_nadvishavaneto_ostava_otkaz_i_sas_zayavka() -> None:
    """Асиметрията не се мени: сбор, по-голям от L, е грешка, не добавка."""

    assert not segments_match_total(
        "F007", 50.0, ДЪЛЖИНА, declared_allowance_cm=ДОБАВКА
    )
    assert not declared_allowance_closes(50.0, ДЪЛЖИНА, 3.0)


def test_obyasnenieto_kazva_koe_chislo_bi_zatvorilo() -> None:
    съобщение = explain_mismatch("F007", СБОР, ДЪЛЖИНА)
    assert "трябва да е 3 cm" in съобщение
    сгрешено = explain_mismatch("F007", СБОР, ДЪЛЖИНА, declared_allowance_cm=5.0)
    assert "не затваря уравнението" in сгрешено


# --------------------------------------------------------------------------- #
# 2. защита на работещото


@pytest.mark.parametrize(
    "код, сбор, дължина, очаквано",
    [
        ("F001", 300.0, 300.0, True),      # прав прът: сборът Е дължината
        ("F001", 295.0, 300.0, False),     # правият прът НЕ се закръгля
        ("F006", 110.0, 115.0, True),      # таванът на затвореното стреме
        ("F006", 100.0, 115.0, False),     # над тавана
        ("F004", 42.0, 45.0, True),        # закръгляне нагоре до 5
        ("F004", 42.0, 47.0, False),       # 47 не е кратно на 5
        ("F009", 189.0, 190.0, True),      # отворено стреме, 1 cm
        ("F007", 50.0, 47.0, False),       # надвишаване
    ],
)
def test_bez_zayavka_nishto_ne_se_menya(код, сбор, дължина, очаквано) -> None:
    """Подразбирането е ``None`` и при него правилата са буква по буква същите."""

    assert segments_match_total(код, сбор, дължина) is очаквано
    assert (
        segments_match_total(код, сбор, дължина, declared_allowance_cm=None)
        is очаквано
    )


def test_zakragljaneto_do_5_ostava_pravilo() -> None:
    """Закръглянето работи СЪЩО и без заявка — заявката не го замества."""

    assert segments_match_total("F004", 42.0, 45.0)
    # а там, където закръглянето не стига, заявката иска ТОЧНОТО число
    assert not segments_match_total("F004", 42.0, 47.0)
    assert not segments_match_total("F004", 42.0, 47.0, declared_allowance_cm=3.0)
    assert segments_match_total("F004", 42.0, 47.0, declared_allowance_cm=5.0)


# --------------------------------------------------------------------------- #
# 3. веригата


def test_master_formulata_priema_samo_zatvaryashtoto_chislo() -> None:
    with pytest.raises(MasterDimensionError):
        parameters_from_segments(
            "F007", (13.0, 18.0, 13.0), production_length_cm=ДЪЛЖИНА
        )
    приети = parameters_from_segments(
        "F007",
        (13.0, 18.0, 13.0),
        production_length_cm=ДЪЛЖИНА,
        declared_allowance_cm=ДОБАВКА,
    )
    assert приети.values == {"L1": 13.0, "L2": 18.0}
    with pytest.raises(MasterDimensionError):
        parameters_from_segments(
            "F007",
            (13.0, 18.0, 13.0),
            production_length_cm=ДЪЛЖИНА,
            declared_allowance_cm=5.0,
        )


def _решение(добавка) -> dict:
    return {
        "schema": "lion-rebar-engineer-decision/v2",
        "action_class": "OVERRIDE",
        "reason_code": "NO_GEOMETRY",
        "reason": "Липсва отделна геометрия",
        "note": "Детайлът е проверен по отпечатаните коти.",
        "annotation": {"handle": "A1", "text": "12N10/s=20"},
        "original": {
            "count": 12,
            "diameter_mm": 10,
            "declared_length_cm": None,
            "shape_code": None,
            "geometry_handles": [],
        },
        "resolved": {
            "count": 12,
            "diameter_mm": 10,
            "production_length_cm": ДЪЛЖИНА,
            "segment_lengths_cm": [13.0, 18.0, 13.0],
            "shape_code": "F007",
            "shape_name": "S ЗА ПРОДЪНВАНЕ",
            "shape_revision": 1,
            "bend_allowance_cm": добавка,
        },
        "geometry": {"handles": [], "layer": "", "basis": "OWNER_ENGINEER_DECISION"},
    }


def test_resheniyeto_bez_zayavka_ne_se_zapisva(tmp_path: Path) -> None:
    """Без число решението пада — точно както преди промяната."""

    with pytest.raises(ValueError) as грешка:
        save_engineer_decision(tmp_path, _решение(None))
    assert "трябва да е 3 cm" in str(грешка.value)


def test_resheniyeto_sas_zayavka_zapisva_chisloto(tmp_path: Path) -> None:
    път = save_engineer_decision(tmp_path, _решение(ДОБАВКА))

    връзка = json.loads(път.read_text(encoding="utf-8"))["links"][0]
    assert връзка["production_bend_allowance_cm"] == pytest.approx(ДОБАВКА)
    assert връзка["manual_assignment"]["production_bend_allowance_cm"] == pytest.approx(
        ДОБАВКА
    )
    # и в одитната следа, за да се вижда КАК е минало решението
    assert връзка["engineer_decision"]["resolved"][
        "bend_allowance_cm"
    ] == pytest.approx(ДОБАВКА)


def test_resheniyeto_otkazva_pogreshno_chislo(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as грешка:
        save_engineer_decision(tmp_path, _решение(5.0))
    assert "не затваря уравнението" in str(грешка.value)


def test_chisloto_ne_se_zapisva_kogato_ne_e_nuzhno(tmp_path: Path) -> None:
    """Решение, минало по измерените правила, не носи число — иначе записът би
    твърдял, че заявката е свършила работа."""

    payload = _решение(ДОБАВКА)
    payload["resolved"]["segment_lengths_cm"] = [15.0, 17.0, 15.0]  # сбор 47 = L
    път = save_engineer_decision(tmp_path, payload)

    връзка = json.loads(път.read_text(encoding="utf-8"))["links"][0]
    assert връзка["production_bend_allowance_cm"] is None


def test_eksportut_chete_sashtoto_pole(tmp_path: Path) -> None:
    """Двата края на веригата говорят за ЕДНО и също поле.

    Най-скъпите дефекти в този проект са били връзки между верни части: записът
    слага едно име, експортът чете друго и решението тихо пада след одобрението.
    """

    път = save_engineer_decision(tmp_path, _решение(ДОБАВКА))
    връзка = json.loads(път.read_text(encoding="utf-8"))["links"][0]

    assert _declared_bend_allowance_of_link(връзка) == pytest.approx(ДОБАВКА)
    assert _declared_bend_allowance_of_link({}) is None


# --------------------------------------------------------------------------- #
# 4. прочитът на полето


@pytest.mark.parametrize("сурово, очаквано", [(None, None), ("", None), ("  ", None)])
def test_praznoto_pole_znachi_po_pravilata(сурово, очаквано) -> None:
    assert declared_bend_allowance({"bend_allowance_cm": сурово}) is очаквано


@pytest.mark.parametrize("сурово", ["0", "-3", "абв", str(DECLARED_BEND_ALLOWANCE_MAX_CM + 1)])
def test_nevalidnoto_chislo_e_greshka_a_ne_malchanie(сурово) -> None:
    """Операторът е написал нещо — трябва да узнае, че не е прието."""

    with pytest.raises(ValueError):
        declared_bend_allowance({"bend_allowance_cm": сурово})


def test_zapetayata_se_chete_kato_desetichen_znak() -> None:
    assert declared_bend_allowance({"bend_allowance_cm": "2,5"}) == pytest.approx(2.5)
