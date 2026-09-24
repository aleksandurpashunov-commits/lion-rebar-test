"""Легендата приема кандидат, решен по НАДПИСАНИТЕ коти — и само него.

ЗАЩО СЪЩЕСТВУВА. „тест-004" (и „тест-005", същият файл): чертежът носи точно
конвенцията, която програмата поддържа — 122 кръгчета с ключове `4a`–`4e` и
легенда `N18 L=410`, `N20 L=420`, `N10 L=360`, `N8 L=360`, `N14 L=385`. Пет от
пет двойки се сдвояват в радиуса, за пет от пет се намира ТОЧНА геометрия.

И въпреки това в индекса влизаха само три. Ключовете `4A` (42 кръгчета) и `4B`
(28) падаха на едно-единствено условие: кандидатът носеше
``DIMENSION_CANDIDATE_TIEBREAK`` — при разлика в дължината **точно 0,0** и
геометрия на 7,14 при допустими 15.

Признакът значи, че печелившият има пълно уравнение на надписаните размери A–E
към общата L, а съперникът му НЯМА такова, при същата форма и същия брой
участъци (``_dimension_evidence_candidate_advantage``). Тоест избрал го е
чертежът със своите коти, а не близостта.

Измерено след промяната: `тест-004` дава 5 от 5 ключа и 122 от 122 кръгчета
(беше 3 и 52), а `d-1`, `s-10` и `s-11` си остават 100%.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf.geometry_candidates import GeometryCandidate, GeometryCandidateAnalysis
from lion_rebar.dxf.position_key_resolver import _legend_analysis_is_exact
from lion_rebar.dxf.recognition_policy import RecognitionPolicy

ПОЛИТИКА = RecognitionPolicy.from_settings({})


def _кандидат(**промени) -> GeometryCandidate:
    данни = {
        "rank": 1,
        "handles": ("A1",),
        "layer": "ARM",
        "segment_count": 3,
        "geometry_length_raw": 410.0,
        "declared_length_cm": 410.0,
        "numeric_difference": 0.0,
        "distance_raw": 7.14,
        "layer_diameter_mm": 18.0,
        "diameter_layer_match": True,
        "score": -0.2,
        "score_gap": 0.4,
        "distance_gap_raw": 10.0,
        "confidence": "СИЛЕН ПО ГЕОМЕТРИЯ — Ø ОТ НАДПИСА",
        "basis": "СЪСТАВНА ГЕОМЕТРИЯ",
        "master_shape_code": "F002",
    }
    данни.update(промени)
    return GeometryCandidate(**данни)


def _анализ(кандидат: GeometryCandidate) -> GeometryCandidateAnalysis:
    return GeometryCandidateAnalysis(
        annotation_handle="P1",
        label="1N18x410",
        diameter_mm=18.0,
        declared_length_cm=410.0,
        candidates=(кандидат,),
        status="НАМЕРЕН Е СИЛЕН КАНДИДАТ",
    )


def test_koti_te_reshavat_ravenstvoto_i_legendata_go_priema() -> None:
    """Точно случаят на `N18 L=410` и `N20 L=420` в „тест-004"."""

    кандидат = _кандидат(review_reason_codes=("DIMENSION_CANDIDATE_TIEBREAK",))
    assert _legend_analysis_is_exact(_анализ(кандидат), ПОЛИТИКА)


def test_bez_nikakva_prichina_za_pregled_pak_minava() -> None:
    """Непроменено поведение за чистия случай — `4C`, `4D`, `4E`."""

    assert _legend_analysis_is_exact(_анализ(_кандидат()), ПОЛИТИКА)


def test_vsyaka_druga_prichina_si_ostava_otkaz() -> None:
    kandidat = _кандидат(review_reason_codes=("GLOBAL_ALTERNATIVE_RECOVERY",))
    assert not _legend_analysis_is_exact(_анализ(kandidat), ПОЛИТИКА)


def test_kotite_ne_izkupvat_druga_prichina() -> None:
    """Отстъпката е за ЕДИН признак, не за списък, в който той участва."""

    кандидат = _кандидат(
        review_reason_codes=("DIMENSION_CANDIDATE_TIEBREAK", "GLOBAL_ALTERNATIVE_RECOVERY"),
    )
    assert not _legend_analysis_is_exact(_анализ(кандидат), ПОЛИТИКА)


def test_dvusmislena_forma_ostava_otkaz() -> None:
    кандидат = _кандидат(
        review_reason_codes=("DIMENSION_CANDIDATE_TIEBREAK",),
        master_ambiguous_shape_codes=("F002", "F012"),
    )
    assert not _legend_analysis_is_exact(_анализ(кандидат), ПОЛИТИКА)


@pytest.mark.parametrize(
    "поле, стойност",
    [
        ("numeric_difference", 5.0),
        ("distance_raw", 40.0),
        ("confidence", "КАНДИДАТ ЗА РЪЧНА ПРОВЕРКА"),
        ("master_shape_code", ""),
    ],
)
def test_ostanalite_usloviya_sa_nepromeneni(поле: str, стойност) -> None:
    """Отстъпката не разхлабва нищо друго: L, разстояние, увереност, форма."""

    кандидат = _кандидат(
        review_reason_codes=("DIMENSION_CANDIDATE_TIEBREAK",), **{поле: стойност}
    )
    assert not _legend_analysis_is_exact(_анализ(кандидат), ПОЛИТИКА)
