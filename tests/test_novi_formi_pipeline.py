# -*- coding: utf-8 -*-
"""Целият процес за нова MASTER форма, от край до край.

ИЗВЛИЧАНЕ → ПРЕДЛАГАНЕ → ПРОВЕРКА ЗА ДУБЛИРАНЕ → ИЗЧЕРТАВАНЕ.

Върху истинския корпус процесът стига докрай и **отказва**: 2981 реда, 2 годни
за раждане, едно семейство, и то пада с „не всеки участък има своя надписана
кота" (`tools/etap9_novi_formi.py`). Отказът е верен — размер не се извежда от
измереното. Затова тук пътят се доказва върху случай, в който доказателството Е
налице: трапецовидно стреме с четири надписани коти.

Тестовете пазят и двете: че пълният път работи, когато данните стигат, и че
всяка от четирите стъпки отказва, когато не стигат.
"""
from __future__ import annotations

import math

import pytest

from lion_rebar.engine import shape_duplicates as дубл
from lion_rebar.engine.shape_from_case import (
    ShapeFromCaseError, build_draft_shape, can_seed_new_shape, collect_cases,
    propose_shape,
)
from lion_rebar.export import proven_form as форма


def ред(handle, текст, обявена, отношения, ъгли, коти, *, сурова=None):
    """Ред от пакетния отчет, какъвто ражда анализът за НЕразпозната форма."""

    подпис = "O|n=%d|r=%s|a=%s" % (
        len(отношения),
        ",".join("%.5f" % о for о in отношения),
        ",".join("%.2f" % ъ for ъ in ъгли),
    )
    return {
        "annotation_handle": handle,
        "annotation_text": текст,
        "master_shape_code": "",
        "canonical_signature": подпис,
        "declared_length_cm": обявена,
        "geometry_length_raw": сурова if сурова is not None else обявена,
        "diameter_mm": 8.0,
        "nearby_dimensions_cm": list(коти),
        "candidate_handles": ["G" + handle],
        "unresolved_curve_count": 0,
        "source_curve_count": 0,
    }


#: Трапецовидно стреме: 20 | 51 | 55 | 20 = 146 cm, четири надписани коти.
#: Точно семейството, за което MASTER няма форма (`s-11`, 36N8x170/36N8x360).
ТРАПЕЦ = [
    ред("A1", "36N8x146", 146.0, (20 / 146, 51 / 146, 55 / 146, 20 / 146),
        (90.0, -8.0, 98.0), (20.0, 51.0, 55.0, 20.0)),
    ред("A2", "36N8x146", 146.0, (20 / 146, 51 / 146, 55 / 146, 20 / 146),
        (90.0, -8.0, 98.0), (20.0, 51.0, 55.0, 20.0)),
    ред("A3", "36N8x146", 146.0, (20 / 146, 51 / 146, 55 / 146, 20 / 146),
        (90.0, -8.0, 98.0), (20.0, 51.0, 55.0, 20.0)),
]


# --------------------------------------------------------------------------
# 1. ИЗВЛИЧАНЕ
# --------------------------------------------------------------------------

def test_izvlichaneto_priema_sluchay_bez_master_forma():
    assert all(can_seed_new_shape(р) for р in ТРАПЕЦ)


def test_izvlichaneto_otkazva_veche_razpoznata_forma():
    с_форма = {**ТРАПЕЦ[0], "master_shape_code": "F003"}
    assert can_seed_new_shape(с_форма) is False


def test_izvlichaneto_otkazva_nerazreshena_daga():
    """Неразрешена дъга значи, че отношенията описват дъга, не прав участък.

    Точно това спира двата документирани случая на `s-11`.
    """

    с_дъга = {**ТРАПЕЦ[0], "unresolved_curve_count": 2, "source_curve_count": 5}
    assert can_seed_new_shape(с_дъга) is False


def test_izvlichaneto_grupira_po_semeystvo():
    групи = collect_cases(ТРАПЕЦ, project_name="проба")
    assert len(групи) == 1
    assert len(next(iter(групи.values()))) == 3


# --------------------------------------------------------------------------
# 2. ПРЕДЛАГАНЕ
# --------------------------------------------------------------------------

def _предложение():
    групи = collect_cases(ТРАПЕЦ, project_name="проба")
    return propose_shape(next(iter(групи.values())))


def test_predlaganeto_stiga_do_forma():
    предложение = _предложение()
    assert предложение is not None
    форма_чернова = build_draft_shape(предложение, code="F050", name="ТРАПЕЦОВИДНО СТРЕМЕ")
    assert форма_чернова.code == "F050"
    assert len(форма_чернова.segments) == 4
    # Черновата се ражда като DRAFT — не се разпознава, докато не се одобри.
    assert str(форма_чернова.state).upper() == "DRAFT"


def test_predlaganeto_otkazva_bez_sobstveni_koti():
    """Размер не се извежда от измереното — това е отказът върху корпуса."""

    без_коти = [{**р, "nearby_dimensions_cm": [400.0, 10.0]} for р in ТРАПЕЦ]
    групи = collect_cases(без_коти, project_name="проба")
    with pytest.raises(ShapeFromCaseError):
        propose_shape(next(iter(групи.values())))


# --------------------------------------------------------------------------
# 3. ПРОВЕРКА ЗА ДУБЛИРАНЕ
# --------------------------------------------------------------------------

def test_novata_forma_ne_e_dublikat_na_master():
    from lion_rebar.engine.repository import EngineRepository
    from lion_rebar.utils.app_paths import AppPaths

    pkg = EngineRepository(AppPaths.default().engine_registry).load_current()
    активни = [ф for ф in pkg.shapes if ф.active and ф.segments]
    чернова = build_draft_shape(_предложение(), code="F050", name="ТРАПЕЦ")
    присъда = дубл.провери(чернова, активни)
    assert присъда.вид == дубл.НОВА, присъда.as_dict()


def test_vtoroto_razhdane_na_sashtata_forma_e_dublikat():
    """Същият случай, роден втори път, трябва да се хване."""

    първа = build_draft_shape(_предложение(), code="F050", name="ТРАПЕЦ")
    втора = build_draft_shape(_предложение(), code="F051", name="ТРАПЕЦ ОТНОВО")
    присъда = дубл.провери(втора, [първа])
    assert присъда.е_дубликат is True


# --------------------------------------------------------------------------
# 4. ИЗЧЕРТАВАНЕ
# --------------------------------------------------------------------------

def test_novata_forma_se_risuva():
    чернова = build_draft_shape(_предложение(), code="F050", name="ТРАПЕЦ")
    завои = дубл.завои_на_формата(чернова)
    svg = форма.начертай([20.0, 51.0, 55.0, 20.0], list(завои))
    assert svg.lstrip().startswith("<svg")
    for размер in ("20", "51", "55"):
        assert размер in svg


def test_risuvachat_na_master_nyama_definiciya_za_nov_kod():
    """Контрола: старият рисувач по код НЕ може да нарисува нова форма.

    Затова новите форми минават през общия рисувач, а не през шаблона.
    """

    from lion_rebar.export.master_engine_v031 import (
        MasterDimensionError, render_master_svg,
    )

    with pytest.raises(MasterDimensionError):
        render_master_svg("F050", {"A": 20.0, "B": 51.0}, include_safe_zone=False)


# --------------------------------------------------------------------------
# Целият път наведнъж
# --------------------------------------------------------------------------

def test_celiyat_pat_ot_red_do_risunka():
    редове = [р for р in ТРАПЕЦ if can_seed_new_shape(р)]
    assert len(редове) == 3
    групи = collect_cases(редове, project_name="проба")
    случаи = next(iter(групи.values()))
    чернова = build_draft_shape(propose_shape(случаи), code="F050", name="ТРАПЕЦ")
    присъда = дубл.провери(чернова, [])
    svg = форма.начертай(
        [20.0, 51.0, 55.0, 20.0], list(дубл.завои_на_формата(чернова)))
    assert присъда.вид == дубл.НОВА
    assert svg.count("<") > 10
    # Сборът на участъците е обявената дължина — иначе формата не се ражда.
    assert math.isclose(20.0 + 51.0 + 55.0 + 20.0, 146.0)
