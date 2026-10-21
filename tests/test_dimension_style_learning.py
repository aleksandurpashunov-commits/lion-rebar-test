# -*- coding: utf-8 -*-
"""Научаването на размерния слой: наблюдение → кандидат → приемане → ползване.

Способността е една: „на кой слой проектантът пише размерите на участъците".
Тестовете пазят и ползата, и границите ѝ — най-вече това, че паметта може само
да СТЕСНИ търсенето след вече настъпил отказ и никога не създава число.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf import chain_printed_dimensions as верига
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.learning import dimension_style as стил
from lion_rebar.learning.knowledge_store import (
    ACCEPTED, CANDIDATE, KnowledgeStore, REJECTED,
)


def _права(handle, слой, начало, край):
    return DxfEntityRecord(handle=handle, entity_type="LINE", layer=слой,
                           geometry={"start": [*начало, 0.0], "end": [*край, 0.0]})


def _текст(handle, слой, текст, точка, височина=10.0):
    return DxfEntityRecord(handle=handle, entity_type="TEXT", layer=слой, text=текст,
                           geometry={"insert": [*точка, 0.0], "height": височина,
                                     "rotation": 0.0})


def _прът(номер, y, *, размерен_слой, стойности=("15", "90", "15"),
          геометричен_слой="ARM"):
    """Прът с три рамена (15/90/15 по начертано ~ 20/90/20) и три размера."""

    п = номер * 1000
    return [
        _права("G%dA" % п, геометричен_слой, (0.0, y + 20.0), (0.0, y)),
        _права("G%dB" % п, геометричен_слой, (0.0, y), (90.0, y)),
        _права("G%dC" % п, геометричен_слой, (90.0, y), (90.0, y + 20.0)),
        _текст("T%dA" % п, размерен_слой, стойности[0], (6.0, y + 10.0)),
        _текст("T%dB" % п, размерен_слой, стойности[1], (45.0, y - 6.0)),
        _текст("T%dC" % п, размерен_слой, стойности[2], (84.0, y + 10.0)),
    ]


def _чертеж(брой, *, размерен_слой="KOTI", шум=()):
    обекти = []
    for и in range(брой):
        обекти.extend(_прът(и + 1, и * 400.0, размерен_слой=размерен_слой))
    обекти.extend(шум)
    return обекти


# --------------------------------------------------------------------------
# Наблюдение
# --------------------------------------------------------------------------

def test_nablyudenieto_namira_razmerniya_sloy():
    н = стил.observe(_чертеж(5))
    предложения = {п["geometry_layer"]: п for п in н["proposals"]}
    assert "ARM" in предложения
    assert предложения["ARM"]["dimension_layer"] == "KOTI"
    assert предложения["ARM"]["support"] == 5
    assert предложения["ARM"]["purity"] == 1.0


def test_smesen_navik_ne_se_predlaga():
    """Два различни размерни слоя за един геометричен слой = нечисто."""

    обекти = _чертеж(3) + [
        *_прът(90, 9000.0, размерен_слой="DRUG"),
    ]
    н = стил.observe(обекти)
    за_arm = [п for п in н["proposals"] if п["geometry_layer"] == "ARM"]
    assert за_arm and за_arm[0]["purity"] < 1.0


def test_nablyudenieto_ne_chete_reshenia_na_analiza():
    """Наблюдението работи само от обектите: няма ред, надпис или форма."""

    н = стил.observe(_чертеж(4))
    сериализуемо = repr(н)
    for забранено in ("declared_length_cm", "master_shape_code", "batch_status"):
        assert забранено not in сериализуемо


# --------------------------------------------------------------------------
# Кандидат и приемане
# --------------------------------------------------------------------------

#: Общ диалект от 14 образеца: независимите източници тук са в ЕДНО стилово
#: семейство, значи приетото е СЕМЕЙНА конвенция (адаптация в семейството).
_ДИАЛЕКТ = tuple("образец %02d #N#" % и for и in range(1, 15))


def _подпис(източник: str) -> dict:
    """Подпис по съдържание: РАЗЛИЧНИ надписи (независим източник), ОБЩ диалект."""

    from lion_rebar.learning.source_identity import signature_from_labels

    return signature_from_labels(
        ["%s надпис %02d" % (източник, и) for и in range(1, 21)],
        patterns=_ДИАЛЕКТ)


def _целеви_стил() -> dict:
    """Диалектът на ЦЕЛЕВИЯ чертеж — в същото семейство като донорите."""

    from lion_rebar.learning.source_identity import style_signature

    return style_signature(_подпис("цел"))


def _наблюдение(семейство, размерен_слой="KOTI", брой=5, locked=False):
    return {"family": семейство, "drawing": семейство, "locked": locked,
            # Независимият източник и подписът му: портата брои източници, не
            # имена (30.09.2026).
            "independence_group": семейство,
            "content_signature": _подпис(семейство),
            "profile": {"dimension_style": стил.observe(
                _чертеж(брой, размерен_слой=размерен_слой))}}


def test_edno_semeystvo_e_samo_kandidat(tmp_path):
    store = KnowledgeStore.load(tmp_path)
    отчет = стил.learn([_наблюдение("A")], store=store)
    assert отчет["accepted"] == []
    assert len(отчет["candidates"]) == 1
    правило = store.get(отчет["candidates"][0]["rule_id"])
    assert правило.state == CANDIDATE


def test_dve_semeystva_priemat_praviloto(tmp_path):
    store = KnowledgeStore.load(tmp_path)
    отчет = стил.learn([_наблюдение("A"), _наблюдение("B")], store=store)
    assert len(отчет["accepted"]) == 1
    правило = store.get(отчет["accepted"][0]["rule_id"])
    assert правило.state == ACCEPTED
    assert правило.role == стил.РОЛЯ
    assert правило.origin
    assert правило.scope["geometry_layer"] == "ARM"
    assert правило.refusal_conditions
    assert len(правило.positives) == 2
    assert правило.counterexamples == []


def test_kontraprimer_othvurlya(tmp_path):
    """Трети чертеж със същия геометричен слой, но друг размерен слой."""

    store = KnowledgeStore.load(tmp_path)
    стил.learn([_наблюдение("A"), _наблюдение("B")], store=store)
    отчет = стил.learn(
        [_наблюдение("A"), _наблюдение("B"),
         _наблюдение("C", размерен_слой="DRUGI_KOTI")],
        store=store)
    assert отчет["accepted"] == []
    assert len(отчет["rejected"]) == 2
    for р in store.all_rules():
        assert р.state == REJECTED
        assert р.counterexamples


def test_oborenoto_pravilo_ne_se_vrushta(tmp_path):
    store = KnowledgeStore.load(tmp_path)
    стил.learn([_наблюдение("A"), _наблюдение("B"),
                _наблюдение("C", размерен_слой="DRUGI_KOTI")], store=store)
    стил.learn([_наблюдение("A"), _наблюдение("B")], store=store)
    assert all(р.state == REJECTED for р in store.all_rules())


def test_zaklyucheno_semeystvo_ne_uchi(tmp_path):
    """Изпитваното семейство не участва в обучението."""

    store = KnowledgeStore.load(tmp_path)
    отчет = стил.learn([_наблюдение("A"), _наблюдение("B", locked=True)], store=store)
    assert отчет["accepted"] == []


def test_praviloto_mozhe_da_se_ottegli(tmp_path):
    store = KnowledgeStore.load(tmp_path)
    отчет = стил.learn([_наблюдение("A"), _наблюдение("B")], store=store)
    ид = отчет["accepted"][0]["rule_id"]
    оттеглено = store.withdraw(ид, replaced_by="друго")
    assert оттеглено.state == "WITHDRAWN"
    assert стил.learned_mapping(store.accepted(),
                                target_style=_целеви_стил()) == {}


# --------------------------------------------------------------------------
# Ползване — и границите му
# --------------------------------------------------------------------------

ЧУЖД = _текст("X1", "DRUG_SLOY", "77", (45.0, 6.0))


def _прът_с_чужд_nadpis():
    """Прът, около който има ЧУЖД числов надпис — днес това е отказ."""

    обекти = _прът(1, 0.0, размерен_слой="KOTI")
    return [*обекти, ЧУЖД]


def test_bez_pamet_chuzhdiyat_nadpis_e_otkaz():
    ук = верига.ВериженУказател(_прът_с_чужд_nadpis())
    assert верига.присъда(["G1000A"], ук, 120.0) is None


def test_s_pamet_chuzhdiyat_nadpis_se_izklyuchva():
    ук = верига.ВериженУказател(_прът_с_чужд_nadpis())
    р = верига.присъда(["G1000A"], ук, 120.0,
                       научени_стилове={"ARM": {"dimension_layer": "KOTI"}})
    assert р is not None
    assert р.участъци_cm == (15.0, 90.0, 15.0)
    assert р.научен_стил == "слой KOTI"


def test_pametta_ne_pipa_veche_uspeshen_prochit():
    """Успелият неограничен прочит не минава през паметта."""

    ук = верига.ВериженУказател(_прът(1, 0.0, размерен_слой="KOTI"))
    без = верига.присъда(["G1000A"], ук, 120.0)
    с = верига.присъда(["G1000A"], ук, 120.0,
                       научени_стилове={"ARM": {"dimension_layer": "GRESHEN"}})
    assert без is not None and с is not None
    assert без.участъци_cm == с.участъци_cm
    assert с.научен_стил == ""


def test_pametta_ne_moze_da_zatvori_greshen_sbor():
    """Дори с научен слой сборът трябва да е обявената дължина."""

    ук = верига.ВериженУказател(_прът_с_чужд_nadpis())
    assert верига.присъда(["G1000A"], ук, 130.0,
                          научени_стилове={"ARM": {"dimension_layer": "KOTI"}}) is None


def test_greshen_nauchen_sloy_ne_vredi():
    """Слой без размери на този прът просто води до отказ, не до число."""

    ук = верига.ВериженУказател(_прът_с_чужд_nadpis())
    assert верига.присъда(["G1000A"], ук, 120.0,
                          научени_стилове={"ARM": {"dimension_layer": "DRUG_SLOY"}}) is None


def test_nauchenoto_e_karta_ot_prietite_pravila(tmp_path):
    store = KnowledgeStore.load(tmp_path)
    стил.learn([_наблюдение("A"), _наблюдение("B")], store=store)
    assert стил.learned_mapping(store.accepted(),
                                target_style=_целеви_стил()) == {"ARM": "KOTI"}


def test_zapisanoto_prezhivyava_prochit(tmp_path):
    """Правилото се чете наново от диска — това е „след рестарт"."""

    store = KnowledgeStore.load(tmp_path)
    стил.learn([_наблюдение("A"), _наблюдение("B")], store=store)
    store.save()
    наново = KnowledgeStore.load(store.path)
    assert стил.learned_mapping(наново.accepted(),
                                target_style=_целеви_стил()) == {"ARM": "KOTI"}


@pytest.mark.parametrize("брой,очаквано", [(3, False), (4, True), (7, True)])
def test_malka_opora_ne_stava_kandidat(брой, очаквано, tmp_path):
    store = KnowledgeStore.load(tmp_path)
    отчет = стил.learn([_наблюдение("A", брой=брой), _наблюдение("B", брой=брой)],
                       store=store)
    assert bool(отчет["accepted"]) is очаквано
