# -*- coding: utf-8 -*-
"""Преднината се мери до най-силния ДРУГ кандидат, не до следващия по-лош.

Възпроизведено от `тест-006 / 8FDA::BJ-FI-L` (`7N10x950`):

| ред | кандидат | score |
|---|---|---|
| 0 | `8E5E` | −0,193875 — по-добър, но законно на друг надпис |
| 1 | `8E71` | −0,191677 — избраният |
| 2 | съставен | −0,030898 — начертан 576 cm при обявени 950 |

Старото измерване даваше на избрания преднина 0,1608 спрямо ЯВНО ГРЕШНИЯ трети.
Когато той отпадна, преднината стана `None`. И двете присъди стъпваха на едно и
също сбъркано измерване.
"""
from __future__ import annotations

import math

import pytest

from lion_rebar.dxf.matcher_evidence import EvidenceBand, _separation_band


def _band(**промени):
    аргументи = {
        "score_gap": None,
        "score_advantage_threshold": 0.04,
        "distance_gap_raw": None,
        "distance_advantage_threshold": 25.0,
        "dimension_candidate_tiebreak": False,
        "alternative_count": 1,
    }
    аргументи.update(промени)
    return _separation_band(**аргументи)


def test_nyama_konkurent_e_yasno() -> None:
    """Само когато НЯМА нито алтернативи, нито записани преднини."""

    band, _съобщение, основа = _band(alternative_count=0)
    assert band is EvidenceBand.EXACT
    assert основа == "NO_ALTERNATIVE"


def test_lipsata_na_po_losh_konkurent_NE_dokazva_pravilnost() -> None:
    """Има алтернатива, но преднина не е записана — това не е доказателство."""

    band, _съобщение, основа = _band(alternative_count=1, score_gap=None)
    assert band is EvidenceBand.REVIEW
    assert основа == "INSUFFICIENT_EVIDENCE"


def test_ravenstvo_ne_e_prednina() -> None:
    """Два еднакво силни кандидата: преднина 0 не минава прага."""

    band, _съобщение, основа = _band(score_gap=0.0)
    # Кодът е по-строг от REVIEW: нулева преднина значи, че друг кандидат е
    # поне толкова силен — това е REJECT, не „за преглед".
    assert band is EvidenceBand.REJECT
    assert основа != "SCORE_GAP"


def test_po_silen_konkurent_dava_otricatelna_prednina() -> None:
    """Когато избраният НЕ е най-силният, преднината е отрицателна.

    Точно случаят на `8FDA`: истинският съперник е с 0,0022 по-добър резултат.
    """

    band, _съобщение, основа = _band(score_gap=-0.0022)
    assert band is EvidenceBand.REJECT
    assert основа != "SCORE_GAP"


def test_izmerima_prednina_minava() -> None:
    band, _съобщение, основа = _band(score_gap=0.16)
    assert band is EvidenceBand.CLEAR
    assert основа == "SCORE_GAP"


# --- самото изчисление ------------------------------------------------------


def _редове(*score_и_разстояние):
    return [
        {"score": с, "distance_raw": р, "handles": ("H%d" % и,),
         "centroid_distance_raw": р, "segment_count": 1}
        for и, (с, р) in enumerate(score_и_разстояние)
    ]


def _преднини(редове):
    """Преизвежда новото правило: до най-силния ДРУГ кандидат."""

    изход = []
    for индекс, ред in enumerate(редове):
        другите = [д for п, д in enumerate(редове) if п != индекс]
        най = min(другите, key=lambda р: float(р["score"])) if другите else None
        изход.append(math.inf if най is None
                     else float(най["score"]) - float(ред["score"]))
    return изход


def test_otpadnal_greshen_kandidat_NE_menya_prednината() -> None:
    """Махането на явно грешен трети кандидат не мести преднината на втория."""

    с_трети = _преднини(_редове((-0.193875, 5.8), (-0.191677, 7.9), (-0.030898, 65.6)))
    без_трети = _преднини(_редове((-0.193875, 5.8), (-0.191677, 7.9)))
    assert с_трети[1] == pytest.approx(-0.002198, abs=1e-6)
    assert без_трети[1] == pytest.approx(-0.002198, abs=1e-6), (
        "преднината на избрания не бива да зависи от явно грешен кандидат")


def test_parviyat_kandidat_zapazva_starото_povedenie() -> None:
    """За водещия кандидат нищо не се мени: преднина спрямо втория."""

    преднини = _преднини(_редове((-0.20, 5.0), (-0.10, 9.0), (-0.05, 20.0)))
    assert преднини[0] == pytest.approx(0.10, abs=1e-9)


def test_edinstven_kandidat_nyama_prednina() -> None:
    assert _преднини(_редове((-0.2, 5.0))) == [math.inf]


# --- производителят, не преизчислението --------------------------------------
#
# Тестовете горе преизвеждаха правилото на ръка. Тези мерят САМИЯ
# `find_geometry_candidates`: преди поправката кандидат №2 от два получаваше
# `score_gap = None` („няма следващ по-лош"), тоест премълчаваше, че пред него
# стои по-силен. Точно това видя `тест-006/8FDA`.

from lion_rebar.dxf.batch_geometry_report import (  # noqa: E402
    _promote_ownership_alternative,
    _prefer_annotation_axis_candidate,
)
from lion_rebar.dxf.geometry_candidates import (  # noqa: E402
    build_geometry_candidate_context,
    find_geometry_candidates,
)
from lion_rebar.dxf.models import DxfEntityRecord  # noqa: E402
from lion_rebar.dxf.recognition_policy import RecognitionPolicy  # noqa: E402
from lion_rebar.engine.package_io import inspect_engine_bundle  # noqa: E402
from lion_rebar.engine.shape_index import EngineShapeIndex  # noqa: E402

from pathlib import Path  # noqa: E402


def _двата_пръта_и_надпис():
    """Мрежа: два еднакво дълги пресичащи се пръта и надпис по оста."""

    хоризонтален = DxfEntityRecord(
        "BAR-H", "LINE", "Arm - N12",
        {"start": [0.0, 0.0, 0.0], "end": [240.0, 0.0, 0.0]})
    вертикален = DxfEntityRecord(
        "BAR-V", "LINE", "Arm - N12",
        {"start": [120.0, -120.0, 0.0], "end": [120.0, 120.0, 0.0]})
    надпис = DxfEntityRecord(
        "ANN", "TEXT", "ARMTXT",
        {"insert": [118.0, 6.0, 0.0], "height": 12.0, "rotation": 0.0},
        "13N12x240")
    return надпис, [хоризонтален, вертикален, надпис]


def _анализирай(надпис, обекти):
    пакет = inspect_engine_bundle(
        Path(__file__).resolve().parents[1] / "engine_updates"
        / "lion-rebar-master_v0.3.14_F005_INDEPENDENT_ARMS_PRODUCTION_DRAFT.lionengine"
    )[0]
    политика = RecognitionPolicy.from_settings(пакет.settings)
    контекст = build_geometry_candidate_context(обекти, policy=политика)
    анализ = find_geometry_candidates(
        надпис, обекти, max_candidates=8, shape_definitions=пакет.shapes,
        control_tests=пакет.tests, policy=политика, context=контекст,
        shape_index=EngineShapeIndex.from_shapes(пакет.shapes))
    return анализ, контекст


def test_vtoriyat_kandidat_priznava_po_silniya() -> None:
    """ПРОИЗВОДИТЕЛЯТ дава отрицателна преднина на втория, не ``None``."""

    надпис, обекти = _двата_пръта_и_надпис()
    анализ, _контекст = _анализирай(надпис, обекти)
    assert len(анализ.candidates) == 2

    водещ, втори = анализ.candidates
    assert водещ.score_gap == pytest.approx(втори.score - водещ.score)
    assert водещ.score_gap > 0
    # Старият код тук даваше None: „няма следващ по-лош". Премълчаваше, че
    # пред втория стои по-силен кандидат.
    assert втори.score_gap is not None
    assert втори.score_gap == pytest.approx(водещ.score - втори.score)
    assert втори.score_gap < 0


def test_razmyanata_po_osta_ne_naslediava_chuzhda_prednina() -> None:
    """След размяната по оста водещият носи СВОЯТА преднина, не чужда.

    Случаят от `Тест-002/702024`: разменен кандидат обявяваше преднина
    0,518 спрямо ДАЛЕЧЕН кандидат, докато истинският съперник е по-добър.
    """

    надпис, обекти = _двата_пръта_и_надпис()
    анализ, контекст = _анализирай(надпис, обекти)
    поправен = _prefer_annotation_axis_candidate(надпис, анализ, контекст)
    водещ = поправен.candidates[0]

    assert водещ.handles == ("BAR-H",)
    # Разменен, значи има по-силен по резултат съперник — и това личи.
    assert водещ.score_gap is not None and водещ.score_gap < 0


def test_dokazano_chuzhd_kandidat_ne_e_saperник() -> None:
    """Пренасоченият кандидат се мери спрямо ОСТАНАЛИТЕ, не спрямо чуждия.

    Без това правило пълната поправка убива Закон 24 мълчаливо: резервният
    кандидат винаги има отрицателна преднина спрямо отхвърления.
    """

    резервен = {
        "rank": 2, "handles": ["A", "B", "C", "D", "E"], "layer": "Arm - N10",
        "segment_count": 5, "geometry_length_raw": 350.0,
        "effective_geometry_length_cm": 350.0, "raw_numeric_difference": 0.0,
        "numeric_difference": 0.0, "distance_raw": 7.9,
        "centroid_distance_raw": 20.0, "layer_diameter_mm": 10.0,
        "diameter_layer_match": True, "confidence": "СИЛЕН КАНДИДАТ — НЕОДОБРЕН",
        "basis": "СВЪРЗАНИ СЕГМЕНТИ", "score": -0.07,
        # Преднината спрямо най-силния ДРУГ кандидат е отрицателна, защото
        # отхвърленият (чуждият) е по-силен. Точно това дава поправката.
        "score_gap": -0.31, "distance_gap_raw": -4.0, "notes": [],
        "canonical_signature": "O|n=5", "master_shape_code": "F010",
        "master_shape_name": "F010", "master_ambiguous_shape_codes": [],
        "dimension_evidence_applied": True, "dimension_total_difference_cm": 0.0,
        "dimension_resolution_strategy": "A_E_TOTAL_MINUS_PRINTED",
        "canonical_segment_lengths_cm": [180.0, 28.0, 19.0, 10.0, 113.0],
        "review_reason_codes": [],
    }
    останал_слаб = {"handles": ["Z"], "score": 0.35, "distance_raw": 90.0}
    ред = {"declared_length_cm": 350.0,
           "alternative_candidates": [резервен, останал_слаб]}

    _promote_ownership_alternative(
        ред, резервен, policy=RecognitionPolicy(exact_length_abs=5.0),
        rejected_candidate={"handles": ["FOREIGN"]}, overlap=["FOREIGN"],
        winner_labels=["8N10x832"])

    assert ред["candidate_score_gap"] == pytest.approx(0.42)
    assert ред["ownership_recovery_verified"] is True
    assert ред["batch_status"] == "СИЛЕН"
