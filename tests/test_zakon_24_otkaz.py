# -*- coding: utf-8 -*-
"""Закон 24: кога свободният резервен кандидат НЯМА право да стане силен.

`recognition_policy` формулира закона така: *„свободен резервен кандидат след
глобално разпределение може да стане силен само при точни A–E, точна обща L,
еднозначен MASTER, съвпадащ Ø от слоя, силна близост и ясно предимство; това пак
не е автоматично одобрение"*.

Клонът, който го прилага, е ``_verified_ownership_alternative``. Досега нямаше
нито един тест върху него — нито положителен, нито отрицателен. Това е опасно
точно защото клонът ПОВИШАВА кандидат: грешка тук ражда силна позиция от нищо,
а „една грешно силна позиция е основание за неприемане".

Затова тестовете са построени като **контрапримери**: една пълна, изпълняваща
всички условия основа, и по един вариант, в който е развалено ТОЧНО ЕДНО
условие. Всеки вариант трябва да даде отказ. Ако утре някое условие изпадне от
кода, точно неговият тест почервенява и се вижда кое е паднало.

Отделно се пази и същината на поправката за ``score_gap``: преднината се мери до
най-силния ОСТАНАЛ кандидат. Съперник, доказано принадлежащ на друг надпис, не е
съперник — но и не е основание преднината да се смята за неограничена.
"""
from __future__ import annotations

import dataclasses

import pytest

from lion_rebar.dxf.batch_geometry_report import (
    _prednina_do_ostanalite,
    _verified_ownership_alternative,
)
from lion_rebar.dxf.recognition_policy import RecognitionPolicy


ОБЯВЕНА = 100.0


def ред(**промени):
    основа = {"declared_length_cm": ОБЯВЕНА}
    основа.update(промени)
    return основа


def кандидат(**промени):
    """Кандидат, който изпълнява ВСИЧКИ условия на закона."""

    основа = {
        "handles": ["AAA"],
        "rank": 2,
        "dimension_evidence_applied": True,     # точни A–E
        "diameter_layer_match": True,           # Ø от слоя съвпада
        "master_ambiguous_shape_codes": [],     # еднозначен MASTER
        "master_shape_code": "F003",
        "canonical_segment_lengths_cm": [30.0, 40.0, 30.0],
        "review_reason_codes": [],
        "numeric_difference": 0.2,              # точна обща L
        "dimension_total_difference_cm": 0.1,
        "distance_raw": 10.0,                   # силна близост
        "score": -0.20,
    }
    основа.update(промени)
    return основа


@pytest.fixture()
def политика():
    return RecognitionPolicy()


# --------------------------------------------------------------------------
# Основата минава — иначе контрапримерите не доказват нищо
# --------------------------------------------------------------------------

def test_pulnata_osnova_minava(политика):
    """Изпълнени са всички условия → повишаването е допустимо."""

    assert _verified_ownership_alternative(
        ред(), кандидат(), политика, score_gap=0.10) is True


# --------------------------------------------------------------------------
# По един контрапример за всяко условие поотделно
# --------------------------------------------------------------------------

@pytest.mark.parametrize("развалено,стойност,какво", [
    ({"dimension_evidence_applied": False}, None, "няма точни A–E"),
    ({"diameter_layer_match": False}, None, "Ø от слоя не съвпада"),
    ({"master_ambiguous_shape_codes": ["F003", "F004"]}, None, "MASTER не е еднозначен"),
    ({"master_shape_code": ""}, None, "няма MASTER форма"),
    ({"master_shape_code": "   "}, None, "MASTER е само интервали"),
    ({"canonical_segment_lengths_cm": []}, None, "няма канонични участъци"),
    ({"numeric_difference": 5.0}, None, "общата дължина не е точна"),
    ({"dimension_total_difference_cm": 3.0}, None, "размерите не затварят"),
    ({"distance_raw": 900.0}, None, "кандидатът е далече"),
    ({"numeric_difference": None}, None, "дължината липсва"),
    ({"distance_raw": "—"}, None, "близостта не е число"),
])
def test_edno_razvaleno_uslovie_e_otkaz(политика, развалено, стойност, какво):
    """Развали едно условие → отказ. Останалите доказателства не го спасяват."""

    assert _verified_ownership_alternative(
        ред(), кандидат(**развалено), политика, score_gap=0.10) is False, какво


@pytest.mark.parametrize("код", [
    "AMBIGUOUS_MASTER", "FAR_ASSOCIATION", "HOOKED_CLOSURE", "MIRROR_REVIEW",
    "NEAR_GAP_HEALED", "SCHEMATIC_SCALE", "UNRESOLVED_CURVE_GEOMETRY",
])
def test_vseki_blokirasht_kod_e_otkaz(политика, код):
    """Всеки от седемте кода за преглед сам по себе си спира повишаването."""

    assert _verified_ownership_alternative(
        ред(), кандидат(review_reason_codes=[код]), политика, score_gap=0.10) is False


def test_izklyuchena_politika_e_otkaz(политика):
    """Изключи ли се възстановяването в политиката, клонът не работи изобщо."""

    изключена = dataclasses.replace(
        политика, verified_alternative_recovery_enabled=False)
    assert изключена.verified_alternative_recovery_enabled is False
    assert _verified_ownership_alternative(
        ред(), кандидат(), изключена, score_gap=0.10) is False


# --------------------------------------------------------------------------
# Преднината — същината на поправката
# --------------------------------------------------------------------------

def test_malka_prednina_e_otkaz(политика):
    """Преднина под прага не стига, колкото и точни да са останалите данни."""

    праг = float(политика.strong_score_gap)
    assert _verified_ownership_alternative(
        ред(), кандидат(), политика, score_gap=праг - 0.001) is False
    assert _verified_ownership_alternative(
        ред(), кандидат(), политика, score_gap=праг) is True


def test_bez_ostanal_konkurent_prednina_ne_se_iska(политика):
    """``None`` значи „няма останал кандидат", не „преднината е неизвестна".

    Това е тясно място и затова е записано: същото правило като ``NO_ALTERNATIVE``
    в разделителя. Ако някой ден ``None`` започне да значи „не е измерена",
    клонът ще повишава при непозната преднина и този тест трябва да се промени
    СЪЗНАТЕЛНО, а не да се нагоди.
    """

    assert _verified_ownership_alternative(
        ред(), кандидат(), политика, score_gap=None) is True


def test_predninata_se_meri_do_OSTANALITE():
    """Отхвърленият съперник не участва в измерването на преднината."""

    избран = {"score": -0.191677}
    всички = [{"score": -0.193875}, избран, {"score": -0.030898}]
    # със съперника, доказано чужд, преднината е отрицателна — тоест няма я
    с_него = _prednina_do_ostanalite([к for к in всички if к is not избран],
                                     избран, "score")
    assert с_него == pytest.approx(-0.002198, abs=1e-6)
    # без него остава само явно грешният трети кандидат
    без_него = _prednina_do_ostanalite([всички[2]], избран, "score")
    assert без_него == pytest.approx(0.160779, abs=1e-6)


def test_bez_ostanali_kandidati_prednina_e_None():
    assert _prednina_do_ostanalite([], {"score": -0.2}, "score") is None


def test_nechislova_prednina_ne_se_izmislya():
    """Кандидат без число за полето просто не участва; не се замества с нула."""

    assert _prednina_do_ostanalite([{"score": None}], {"score": -0.2}, "score") is None
    assert _prednina_do_ostanalite([{"score": -0.1}], {"score": "—"}, "score") is None
