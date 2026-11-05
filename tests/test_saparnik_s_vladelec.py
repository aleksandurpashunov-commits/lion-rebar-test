# -*- coding: utf-8 -*-
"""Съперник, който доказано принадлежи на друг надпис, не е съперник.

## Случаят

`SS-1`, „7N12x950": прав прът 950 до самия надпис (`C24F`, 7,9 единици).
На 14,7 единици има втори прав прът 950 (`C23B`) — но той е ПЪРВИЯТ
кандидат на съседния „39N12x950", който е силен сам по себе си (преднина
0,151 по score, 143,8 единици по разстояние, точна L). Преднината на
`C24F` се мереше и срещу `C23B` — 0,007 — и позицията спираше с
``WEAK_CANDIDATE_SEPARATION``.

Същото правило вече важи в обратната посока: когато първият кандидат
бъде отнет от по-силен надпис, резервният се мери само срещу ОСТАНАЛИТЕ
възможни (`_promote_ownership_alternative`). Тук то се прилага и когато
първият кандидат си остава свой.

## Границите, които тестовете пазят

Съперникът се изключва само ако е първият кандидат на позиция, която е
силна БЕЗ това правило: силно състояние, точна L, еднозначен MASTER и
собствена ясна преднина. Свободен съперник остава съперник; двама слаби
не се освобождават взаимно; изключеният съперник не прави позицията
силна, ако L, слоят или разстоянието не стигат.
"""
from __future__ import annotations

from lion_rebar.dxf.batch_geometry_report import (
    _apply_global_geometry_conflicts,
    _resolve_global_geometry_ownership,
)
from lion_rebar.dxf.matcher_evidence import evaluate_report_row
from lion_rebar.dxf.recognition_policy import RecognitionPolicy

ПОЛИТИКА = RecognitionPolicy()


def алт(handles, *, score, distance, raw_difference=0.0, rank=2):
    return {
        "rank": rank,
        "handles": list(handles),
        "layer": "Arm - N12",
        "score": score,
        "distance_raw": distance,
        "raw_numeric_difference": raw_difference,
        "numeric_difference": raw_difference,
    }


def ред(handle, text, handles, *, declared, score, distance, score_gap,
        distance_gap, alternatives, status="СИЛЕН",
        confidence="СИЛЕН КАНДИДАТ — НЕОДОБРЕН", raw_difference=0.0,
        layer_diameter=12.0, layer_match=True, ambiguous=()):
    return {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": handle,
        "annotation_text": text,
        "diameter_mm": 12.0,
        "declared_length_cm": declared,
        "batch_status": status,
        "candidate_confidence": confidence,
        "candidate_handles": list(handles),
        "candidate_layer": "Arm - N12",
        "segment_count": 1,
        "geometry_length_raw": declared + raw_difference,
        "effective_geometry_length_cm": declared + raw_difference,
        "raw_numeric_difference": raw_difference,
        "numeric_difference": raw_difference,
        "distance_raw": distance,
        "centroid_distance_raw": distance,
        "layer_diameter_mm": layer_diameter,
        "diameter_layer_match": layer_match,
        "candidate_score": score,
        "candidate_score_gap": score_gap,
        "candidate_distance_gap_raw": distance_gap,
        "global_conflict": False,
        "master_conflict": bool(ambiguous),
        "master_shape_code": "F001",
        "master_shape_name": "ПРАВ ПРЪТ",
        "master_reference_profile_id": "T-F001-001",
        "master_ambiguous_shape_codes": list(ambiguous),
        "canonical_signature": "O|n=1|r=1.00000|a=",
        "review_reason_codes": [],
        "candidate_notes": [],
        "analysis_warnings": [],
        "alternative_candidates": list(alternatives),
    }


def седем_950(**промени):
    """`SS-1/C33F`: свой прът C24F, съперник C23B на 14,7."""

    основа = dict(
        handle="C33F", text="7N12x950", handles=["C24F"], declared=950.0,
        score=-0.19168, distance=7.907, score_gap=0.00714, distance_gap=6.78,
        status="ЗА РЪЧЕН ПРЕГЛЕД", confidence="КАНДИДАТ ЗА РЪЧНА ПРОВЕРКА",
        alternatives=[алт(["C23B"], score=-0.18454, distance=14.688)],
    )
    основа.update(промени)
    return ред(**основа)


def тридесет_и_девет_950(**промени):
    """`SS-1`, „39N12x950": първият му кандидат е C23B, ясно отделен."""

    основа = dict(
        handle="C2xx", text="39N12x950", handles=["C23B"], declared=950.0,
        score=-0.19168, distance=7.907, score_gap=0.15134, distance_gap=143.77,
        alternatives=[алт(["C24F"], score=-0.04034, distance=151.68)],
    )
    основа.update(промени)
    return ред(**основа)


def прекарай(rows):
    _resolve_global_geometry_ownership(rows, ПОЛИТИКА)
    _apply_global_geometry_conflicts(rows)
    return rows


def спирачки(row):
    решение = evaluate_report_row(row, approved_master_codes={"F001"}, policy=ПОЛИТИКА)
    return {gate.code for gate in решение.safety_gates}


# --------------------------------------------------------------------------
# Същината
# --------------------------------------------------------------------------

def test_saparnikat_na_silen_sased_ne_e_saparnik():
    седем, тридесет = прекарай([седем_950(), тридесет_и_девет_950()])

    assert седем["candidate_handles"] == ["C24F"]
    assert седем["batch_status"] == "СИЛЕН"
    assert седем["rivals_owned_elsewhere"] == [["C23B"]]
    assert "WEAK_CANDIDATE_SEPARATION" not in спирачки(седем)
    # Съседът не е пипнат.
    assert тридесет["candidate_handles"] == ["C23B"]
    assert тридесет["batch_status"] == "СИЛЕН"
    assert not спирачки(тридесет)


def test_veche_silniyat_red_gubi_spirachkata():
    """`SS-1/D8FA` „7N12x200": СИЛЕН, но по-близкият D8F1 е на „6N12x200"."""

    седем = ред("D8FA", "7N12x200", ["D8F2"], declared=200.0, score=-0.15773,
                distance=8.454, score_gap=-0.02564, distance_gap=-5.127,
                alternatives=[алт(["D8F1"], score=-0.18336, distance=3.327, rank=1)])
    шест = ред("D8EE", "6N12x200", ["D8F1"], declared=200.0, score=-0.16232,
               distance=7.537, score_gap=0.09683, distance_gap=19.37,
               alternatives=[алт(["D8F2"], score=-0.06549, distance=26.90)])
    assert "WEAK_CANDIDATE_SEPARATION" in спирачки(седем)

    прекарай([седем, шест])

    assert седем["batch_status"] == "СИЛЕН"
    assert "WEAK_CANDIDATE_SEPARATION" not in спирачки(седем)


# --------------------------------------------------------------------------
# Границите
# --------------------------------------------------------------------------

def test_svobodniyat_saparnik_ostava_saparnik():
    """Положителна контрола: без собственик на C23B спирачката остава."""

    (седем,) = прекарай([седем_950()])

    assert седем["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert "rivals_owned_elsewhere" not in седем
    assert "WEAK_CANDIDATE_SEPARATION" in спирачки(седем)


def test_blizak_svoboden_saparnik_ne_se_skriva_zad_chuzhdiya():
    """Изключва се само чуждият; близкият свободен прът продължава да спори."""

    седем = седем_950(alternatives=[
        алт(["C23B"], score=-0.18454, distance=14.688),
        # 0,012 по score и 7,1 единици — и двете под праговете 0,04 и 8.
        алт(["C250"], score=-0.18000, distance=15.0, rank=3),
    ])
    седем, _ = прекарай([седем, тридесет_и_девет_950()])

    assert седем["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert "WEAK_CANDIDATE_SEPARATION" in спирачки(седем)
    # Редът остава, както е: нищо не е записано наполовина.
    assert "rivals_owned_elsewhere" not in седем
    assert седем["candidate_score_gap"] == 0.00714


def test_slab_sobstvenik_ne_e_dokazatelstvo():
    """Съседът, който сам е без ясна преднина, не доказва нищо."""

    седем, _ = прекарай([
        седем_950(),
        тридесет_и_девет_950(score_gap=0.01, distance_gap=2.0,
                              status="ЗА РЪЧЕН ПРЕГЛЕД",
                              confidence="КАНДИДАТ ЗА РЪЧНА ПРОВЕРКА"),
    ])

    assert седем["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert "WEAK_CANDIDATE_SEPARATION" in спирачки(седем)


def test_silno_sastoyanie_bez_sobstvena_prednina_ne_dokazva():
    """Състоянието „СИЛЕН" не стига: `SS-1/D8FA` е СИЛЕН с преднина −0,026.

    Собственикът трябва да е отделен от СВОИТЕ съперници, иначе и неговото
    владение е само близост.
    """

    седем, _ = прекарай([
        седем_950(),
        тридесет_и_девет_950(score_gap=0.01, distance_gap=2.0),
    ])

    assert седем["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert "WEAK_CANDIDATE_SEPARATION" in спирачки(седем)


def test_dvama_slabi_ne_se_osvobozhdavat_vzaimno():
    """Всеки е първи кандидат на другия съперник — никой не печели."""

    едно = седем_950()
    друго = седем_950(handle="C2xx", text="39N12x950", handles=["C23B"],
                      alternatives=[алт(["C24F"], score=-0.18454, distance=14.688)])
    едно, друго = прекарай([едно, друго])

    for row in (едно, друго):
        assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
        assert "WEAK_CANDIDATE_SEPARATION" in спирачки(row)


def test_sporniyat_prat_ne_e_nichiy_dokazan_prat():
    """`d-2`: прътът на „собственика" е първи кандидат и на трети надпис.

    Двойното владение се отбелязва едва след тази стъпка; тук се брои наново.
    """

    трети = тридесет_и_девет_950(handle="C3xx", text="5N12x950",
                                 alternatives=[])
    седем, _, _ = прекарай([седем_950(), тридесет_и_девет_950(), трети])

    assert седем["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert "WEAK_CANDIDATE_SEPARATION" in спирачки(седем)


def test_yasno_otdeleniyat_red_ne_se_pipa():
    """`тест-006` „3N25x980": отделен по разстояние; преднината не се мери наново.

    Първата версия пресмяташе наново и ЯСНО отделени редове и на шест от тях
    отне преднината (корпус 04.11.2026).
    """

    отделен = седем_950(score=-0.19168, score_gap=0.01, distance_gap=9.0,
                        status="СИЛЕН", confidence="СИЛЕН КАНДИДАТ — НЕОДОБРЕН",
                        alternatives=[алт(["C23B"], score=-0.18454, distance=14.688)])
    отделен, _ = прекарай([отделен, тридесет_и_девет_950()])

    assert отделен["candidate_score_gap"] == 0.01
    assert отделен["candidate_distance_gap_raw"] == 9.0
    assert "rivals_owned_elsewhere" not in отделен
    assert "WEAK_CANDIDATE_SEPARATION" not in спирачки(отделен)


def test_sobstvenik_s_netochna_L_ne_dokazva():
    """„3N10x630" държи прът 640: L не е точна, владението не е доказано."""

    седем, _ = прекарай([
        седем_950(),
        тридесет_и_девет_950(text="39N12x940", declared=940.0, raw_difference=10.0),
    ])

    assert седем["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert "WEAK_CANDIDATE_SEPARATION" in спирачки(седем)


def test_sobstvenik_s_dvusmislen_master_ne_dokazva():
    седем, _ = прекарай([
        седем_950(),
        тридесет_и_девет_950(ambiguous=("F001", "F002")),
    ])

    assert седем["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"


def test_osvobodeniyat_s_greshen_sloy_ne_stava_silen():
    """Изключеният съперник не заличава друга спирачка — тук слоя."""

    седем, _ = прекарай([
        седем_950(layer_diameter=14.0, layer_match=False),
        тридесет_и_девет_950(),
    ])

    assert седем["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert "LAYER_MISMATCH" in спирачки(седем)


def test_osvobodeniyat_s_netochna_L_ne_stava_silen():
    седем, _ = прекарай([
        седем_950(raw_difference=30.0),
        тридесет_и_девет_950(),
    ])

    assert седем["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
