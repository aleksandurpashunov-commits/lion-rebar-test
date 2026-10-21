# -*- coding: utf-8 -*-
"""Преизползване на операторско решение — намирането срещу разрешението.

Всеки тест стъпва на РЕАЛНИТЕ производствени структури: пликът на решението се
записва през ``session.decision_contract.append_project_decision_event`` и се
доставя през ``mark_project_decision_delivered`` — същите функции, през които
минават и прозорецът, и безинтерфейсният път.

Защо тестовете са нови, а не разширение на ``test_pamet_v_analiza.py``: там се
мери СТАРИЯТ строг указател, който търси по изхода на разпознаването. Тук се
мери новото разделяне на етапите.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lion_rebar.review import decision_reuse as пр
from lion_rebar.review.decision_suggestions import strict_context_from_review_row
from lion_rebar.session.decision_contract import (
    DecisionWithdrawError,
    JOURNAL_NAME,
    append_project_decision_event,
    mark_project_decision_delivered,
    withdraw_project_decision,
    withdrawn_decision_ids,
)

САМОЛИЧНОСТ_ДОНОР = {
    "project_id": "PROEKT-DONOR",
    "source_sha256": "a" * 64,
    "source_generation_id": "GEN-DONOR",
    "analysis_run_id": "RUN-DONOR",
}
САМОЛИЧНОСТ_ЦЕЛ = {
    "project_id": "PROEKT-CEL",
    "source_sha256": "b" * 64,
    "source_generation_id": "GEN-CEL",
    "analysis_run_id": "RUN-CEL",
}


def _проект(корен: Path, самоличност: dict) -> Path:
    работна = корен / "working"
    работна.mkdir(parents=True, exist_ok=True)
    (работна / "project.json").write_text(
        json.dumps(
            {
                "name": корен.name,
                "project_id": самоличност["project_id"],
                "engine_id": "lion-rebar-master",
                "engine_version": "0.3.15",
                "engine_sha256": "c" * 64,
                "extensions": {"data_identity": самоличност},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return корен


def _решение(
    корен: Path,
    *,
    надпис: str,
    бройка: int,
    диаметър: float,
    обявена: float,
    форма: str,
    участъци: list[float],
    производствена: float | None = None,
    действие: str = "OVERRIDE",
    причина: str = "NO_GEOMETRY",
    handle: str = "81E9::BJ-FI-L",
    геометрия: list[str] | None = None,
    самоличност: dict | None = None,
    доставено: bool = True,
    допълнително: dict | None = None,
) -> dict:
    товар = {
        "annotation_handle": handle,
        "annotation_text": надпис,
        "action_class": действие,
        "reason_code": причина,
        "reason": "тест",
        "note": "тестова бележка на донора",
        "actor": "Тестов оператор",
        "data_identity": самоличност or САМОЛИЧНОСТ_ДОНОР,
        "original": {
            "count": бройка,
            "diameter_mm": диаметър,
            "declared_length_cm": обявена,
            "shape_code": None,
            "segment_lengths_cm": [],
            "geometry_handles": геометрия or [],
            "batch_status": "НЯМА КАНДИДАТ",
        },
        "resolved": {
            "count": бройка,
            "diameter_mm": диаметър,
            "production_length_cm": (
                обявена if производствена is None else производствена
            ),
            "shape_code": форма,
            "shape_name": форма,
            "shape_revision": 1,
            "segment_lengths_cm": участъци,
            "turn_angles_deg": [],
        },
        "geometry": {"handles": геометрия or []},
    }
    if допълнително:
        товар.update(допълнително)
    плик = append_project_decision_event(корен, "ENGINEER_RESOLVED", товар)
    if доставено:
        mark_project_decision_delivered(
            корен, плик["event_id"], session_id="S-TEST"
        )
    return плик


def _ред_без_кандидат(
    *,
    надпис: str = "350N12x300",
    бройка: int = 350,
    диаметър: float = 12.0,
    обявена: float = 300.0,
    handle: str = "81E9::BJ-FI-L",
) -> dict:
    """Ред, който разпознаването е оставило БЕЗ кандидат — реалният случай."""

    return {
        "annotation_handle": handle,
        "annotation_text": надпис,
        "annotation_class": "ACTIONABLE",
        "annotation_count": бройка,
        "diameter_mm": диаметър,
        "declared_length_cm": обявена,
        "batch_status": "НЯМА КАНДИДАТ",
        "master_shape_code": None,
        "canonical_segment_lengths_cm": [],
        "canonical_turn_angles_deg": [],
        "candidate_handles": [],
        "nearby_dimensions_cm": [],
        "matcher_blocker_codes": [
            "NO_CANDIDATE", "NO_MASTER_MATCH", "LENGTH_NOT_COMPARABLE",
        ],
        "matcher_disposition": "UNKNOWN",
        "review_reason_codes": [],
        "data_identity": dict(САМОЛИЧНОСТ_ЦЕЛ),
        "global_conflict": False,
        "geometry_owner_conflict": False,
        "master_conflict": False,
    }


# ---------------------------------------------------------------------------
# Етап 1 — намирането не иска кандидат
# ---------------------------------------------------------------------------
def test_stariyat_strog_kluch_otkazva_red_bez_kandidat():
    """Положителна контрола: СТАРОТО поведение отказва точно този ред.

    Без тази проверка следващият тест не доказва нищо: щеше да е възможно
    старият указател също да намира случая и новият да не добавя нищо.
    """

    контекст, липсва = strict_context_from_review_row(
        _ред_без_кандидат(),
        identity=САМОЛИЧНОСТ_ЦЕЛ,
        engine_version="0.3.15",
    )
    assert контекст is None
    assert "candidate_shape_code" in липсва


def test_namiraneto_ne_iska_kandidat(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0],
    )
    указател = пр.Указател.от_проект(донор)
    вход, намерени, липсва = указател.намери(
        _ред_без_кандидат(), самоличност=САМОЛИЧНОСТ_ЦЕЛ
    )
    assert липсва == ()
    assert вход is not None
    assert len(намерени) == 1
    assert намерени[0].правило == пр.ПРАВИЛО_ПРАВ_БЕЗ_ГЕОМЕТРИЯ


def test_vhodniyat_kluch_ne_zavisi_ot_razpoznavaneto():
    """Ключът е еднакъв при НЯМА КАНДИДАТ и при напълно разпознат ред."""

    беден = _ред_без_кандидат()
    богат = dict(беден)
    богат.update({
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "master_shape_code": "F002",
        "canonical_segment_lengths_cm": [110.0, 40.0, 150.0],
        "candidate_handles": ["7CE0", "7CE1", "7CE2"],
        "matcher_disposition": "CONTROL",
        "matcher_blocker_codes": [],
    })
    първи, _ = пр.входни_доказателства(беден)
    втори, _ = пр.входни_доказателства(богат)
    assert първи is not None and втори is not None
    assert първи.ключ == втори.ключ


# ---------------------------------------------------------------------------
# Етап 3 — проверката поле по поле
# ---------------------------------------------------------------------------
def test_prav_prat_bez_geometriya_e_prilozhim(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0],
    )
    указател = пр.Указател.от_проект(донор)
    предложение = указател.предложи(
        _ред_без_кандидат(),
        самоличност=САМОЛИЧНОСТ_ЦЕЛ,
        активни_форми={"F001": 1},
    )
    assert предложение.състояние == пр.ПРИЛОЖИМО
    assert предложение.може_да_се_приеме is True
    по_име = {поле.име: поле for поле in предложение.полета}
    assert по_име["segment_lengths_cm"].стойност == [300.0]
    # Числото идва от ПРАВИЛОТО на програмата върху обявената дължина на
    # ЦЕЛТА, не от донора.
    assert по_име["segment_lengths_cm"].източник == пр.ОТ_ПРАВИЛО
    assert по_име["count"].източник == пр.ОТ_ЦЕЛТА
    assert по_име["shape_code"].източник == пр.ОТ_ФОРМАТА
    assert all(поле.присъда == пр.ПОДКРЕПЕНО for поле in предложение.полета)


def test_donorska_popravka_na_dalzhinata_ne_e_prenosimo_pravilo(tmp_path):
    """Инженерът е сменил 300 на 305 — тогава „участъкът е обявената" е грешно.

    Донорът носи знание, което НЕ е в надписа. Правилото не е преносимо и
    числото 305 не бива да стигне до целевата позиция по никакъв път.
    """

    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[305.0], производствена=305.0,
    )
    указател = пр.Указател.от_проект(донор)
    случай = указател.всички()[0]
    assert случай.правило == пр.БЕЗ_ПРЕНОСИМО_ПРАВИЛО
    предложение = указател.предложи(
        _ред_без_кандидат(),
        самоличност=САМОЛИЧНОСТ_ЦЕЛ,
        активни_форми={"F001": 1},
    )
    assert предложение.състояние == пр.НЕПРИЛОЖИМО
    assert предложение.може_да_се_приеме is False
    assert "305" not in json.dumps(
        [поле.речник() for поле in предложение.полета], ensure_ascii=False
    )


def test_2170N10x47_ne_polucava_prilozhimo_reshenie(tmp_path):
    """Известният случай: конкретните размери на донора НЕ се пренасят.

    Измерено в корпуса: същият надпис, същата бройка, същият диаметър, същата
    обявена дължина и същите блокери — а инженерът е решил ``13/21/13`` в
    ``s-9`` и ``11/25/11`` в ``s-8``. Еднаквият вход не доказва еднакъв
    отговор, затова тук се отказва.
    """

    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="2170N10x47", бройка=2170, диаметър=10.0, обявена=47.0,
        форма="F007", участъци=[13.0, 21.0, 13.0], handle="9074::BJ-FI-L",
    )
    указател = пр.Указател.от_проект(донор)
    ред = _ред_без_кандидат(
        надпис="2170N10x47", бройка=2170, диаметър=10.0, обявена=47.0,
        handle="9074::BJ-FI-L",
    )
    # Целевата позиция носи СВОИ коти, но те не дават три рамена със сбор 47.
    ред["nearby_dimensions_cm"] = [36.5, 50.0, 25.0, 20.0]
    предложение = указател.предложи(
        ред, самоличност=САМОЛИЧНОСТ_ЦЕЛ, активни_форми={"F007": 1}
    )
    assert предложение.състояние == пр.САМО_ФОРМА
    assert предложение.може_да_се_приеме is False
    стойности = json.dumps(
        [поле.стойност for поле in предложение.полета], ensure_ascii=False
    )
    for донорско in ("13", "21"):
        assert "%s.0" % донорско not in стойности
    with pytest.raises(пр.ПредложениетоНеЕПриложимо):
        предложение.за_приемане(изрично_прието=True)


def test_samo_forma_е_vidimo_no_ne_se_priema(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="4359N10x97", бройка=4359, диаметър=10.0, обявена=97.0,
        форма="F008", участъци=[15.0, 18.5, 30.0, 18.5, 15.0],
        действие="CORRECTION", причина="WRONG_SHAPE", handle="8A50::BJ-FI-L",
        геометрия=["8A44"],
    )
    указател = пр.Указател.от_проект(донор)
    ред = _ред_без_кандидат(
        надпис="4359N10x97", бройка=4359, диаметър=10.0, обявена=97.0,
        handle="8A50::BJ-FI-L",
    )
    ред.update({
        "batch_status": "РИСК",
        "master_shape_code": "F001",
        "candidate_handles": ["8A44"],
        "segment_count": 1,
        "matcher_blocker_codes": ["LENGTH_MISMATCH"],
    })
    предложение = указател.предложи(
        ред, самоличност=САМОЛИЧНОСТ_ЦЕЛ, активни_форми={"F008": 1}
    )
    assert предложение.състояние == пр.САМО_ФОРМА
    assert предложение.може_да_се_приеме is False
    assert предложение.остава_за_оператора
    assert any(
        "5 участъка" in текст for текст in предложение.остава_за_оператора
    )


def test_uchastatsite_na_celta_se_polzvat_kogato_ya_doкazvat(tmp_path):
    """k рамена: размерите идват от ЦЕЛТА, когато тя сама ги носи."""

    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="8N8x435", бройка=8, диаметър=8.0, обявена=435.0,
        форма="F006", участъци=[15.0, 405.0, 15.0], handle="14736::BJ-FI-L",
        геометрия=["14730"], действие="CORRECTION", причина="WRONG_SHAPE",
    )
    указател = пр.Указател.от_проект(донор)
    ред = _ред_без_кандидат(
        надпис="8N8x435", бройка=8, диаметър=8.0, обявена=435.0,
        handle="D0CB::BJ-FI-L",
    )
    ред.update({
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        # СВОИ участъци на целевата позиция, различни от донорските.
        "canonical_segment_lengths_cm": [20.0, 395.0, 20.0],
        "candidate_handles": ["D0C1", "D0C2", "D0C3"],
        "segment_count": 3,
    })
    предложение = указател.предложи(
        ред, самоличност=САМОЛИЧНОСТ_ЦЕЛ, активни_форми={"F006": 1}
    )
    assert предложение.състояние == пр.ПРИЛОЖИМО
    по_име = {поле.име: поле for поле in предложение.полета}
    # СВОИТЕ числа, не донорските 15/405/15.
    assert по_име["segment_lengths_cm"].стойност == [20.0, 395.0, 20.0]
    assert по_име["segment_lengths_cm"].източник == пр.ОТ_ЦЕЛТА


def test_razlichniyat_broy_ramena_protivorechi(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="8N8x435", бройка=8, диаметър=8.0, обявена=435.0,
        форма="F006", участъци=[15.0, 405.0, 15.0], handle="14736::BJ-FI-L",
        геометрия=["14730"], действие="CORRECTION", причина="WRONG_SHAPE",
    )
    указател = пр.Указател.от_проект(донор)
    ред = _ред_без_кандидат(
        надпис="8N8x435", бройка=8, диаметър=8.0, обявена=435.0,
        handle="D0CB::BJ-FI-L",
    )
    ред.update({
        "master_shape_code": "F002",
        "segment_count": 5,
        "canonical_segment_lengths_cm": [10.0, 10.0, 395.0, 10.0, 10.0],
        "candidate_handles": ["D0C1"],
    })
    предложение = указател.предложи(
        ред, самоличност=САМОЛИЧНОСТ_ЦЕЛ, активни_форми={"F006": 1}
    )
    assert предложение.състояние == пр.НЕПРИЛОЖИМО
    assert "shape_code" in предложение.липсва


# ---------------------------------------------------------------------------
# Защитите, които НЕ се разхлабват
# ---------------------------------------------------------------------------
def test_potvarzhdenieto_ne_e_popravka(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], действие="CONFIRMATION",
        причина="CONFIRM_CORRECT",
    )
    указател = пр.Указател.от_проект(донор)
    assert указател.всички() == ()
    assert указател.сводка.отхвърлени.get("НЕ_Е_ДОКАЗАТЕЛСТВО") == 1


def test_izklyuchvashtoto_reshenie_ne_se_prenasya(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="2170N10x47", бройка=2170, диаметър=10.0, обявена=47.0,
        форма="F007", участъци=[13.0, 21.0, 13.0], действие="EXCLUSION",
        причина="NON_PRODUCTION_POSITION", handle="9074::BJ-FI-L",
    )
    assert пр.Указател.от_проект(донор).всички() == ()


def test_nedostavenoto_reshenie_ne_e_dokazatelstvo(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], доставено=False,
    )
    указател = пр.Указател.от_проект(донор)
    assert указател.всички() == ()
    assert указател.сводка.отхвърлени.get("НЕДОСТАВЕНО") == 1


def test_sporno_vladenie_spira_preizpolzvaneto(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0],
    )
    указател = пр.Указател.от_проект(донор)
    ред = _ред_без_кандидат()
    ред["geometry_owner_conflict"] = True
    предложение = указател.предложи(
        ред, самоличност=САМОЛИЧНОСТ_ЦЕЛ, активни_форми={"F001": 1}
    )
    assert предложение.състояние == пр.НЕПРИЛОЖИМО
    assert "владение" in предложение.обяснение


def test_dvusmislen_master_spira_preizpolzvaneto(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0],
    )
    ред = _ред_без_кандидат()
    ред["master_conflict"] = True
    предложение = пр.Указател.от_проект(донор).предложи(
        ред, самоличност=САМОЛИЧНОСТ_ЦЕЛ, активни_форми={"F001": 1}
    )
    assert предложение.състояние == пр.НЕПРИЛОЖИМО


def test_formata_s_druga_revizia_ne_se_prenasya(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0],
    )
    предложение = пр.Указател.от_проект(донор).предложи(
        _ред_без_кандидат(),
        самоличност=САМОЛИЧНОСТ_ЦЕЛ,
        активни_форми={"F001": 2},
    )
    assert предложение.състояние == пр.НЕПРИЛОЖИМО
    assert "ревизия" in предложение.обяснение


def test_neaktivna_forma_ne_se_prenasya(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0],
    )
    предложение = пр.Указател.от_проект(донор).предложи(
        _ред_без_кандидат(),
        самоличност=САМОЛИЧНОСТ_ЦЕЛ,
        активни_форми={"F002": 1},
    )
    assert предложение.състояние == пр.НЕПРИЛОЖИМО
    assert "не е активна" in предложение.обяснение


def test_sashtata_poziciya_ne_predlaga_na_sebe_si(tmp_path):
    проект = _проект(tmp_path / "edin", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        проект, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], handle="81E9::BJ-FI-L",
    )
    указател = пр.Указател.от_проект(проект)
    ред = _ред_без_кандидат(handle="81E9::BJ-FI-L")
    ред["data_identity"] = dict(САМОЛИЧНОСТ_ДОНОР)
    предложение = указател.предложи(
        ред, самоличност=САМОЛИЧНОСТ_ДОНОР, активни_форми={"F001": 1}
    )
    assert предложение.състояние == пр.НЕНАМЕРЕНО


def test_dva_razlichni_otgovora_sa_protivorechie(tmp_path):
    първи = _проект(tmp_path / "d1", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        първи, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], handle="81E9::BJ-FI-L",
    )
    втори = _проект(
        tmp_path / "d2",
        {**САМОЛИЧНОСТ_ДОНОР, "project_id": "PROEKT-D2",
         "source_generation_id": "GEN-D2", "source_sha256": "d" * 64},
    )
    _решение(
        втори, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F006", участъци=[100.0, 100.0, 100.0], handle="99AA::BJ-FI-L",
        геометрия=["99A1"], действие="CORRECTION", причина="WRONG_SHAPE",
        самоличност={
            "project_id": "PROEKT-D2", "source_sha256": "d" * 64,
            "source_generation_id": "GEN-D2", "analysis_run_id": "RUN-D2",
        },
    )
    указател = пр.Указател.от_проект(първи, допълнителни=[втори])
    предложение = указател.предложи(
        _ред_без_кандидат(),
        самоличност=САМОЛИЧНОСТ_ЦЕЛ,
        активни_форми={"F001": 1, "F006": 1},
    )
    assert предложение.състояние == пр.ПРОТИВОРЕЧИЕ
    assert предложение.може_да_се_приеме is False
    with pytest.raises(пр.ПредложениетоНеЕПриложимо):
        предложение.за_приемане(изрично_прието=True)


def test_bez_izrichno_priemane_nishto_ne_se_popalva(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0],
    )
    предложение = пр.Указател.от_проект(донор).предложи(
        _ред_без_кандидат(),
        самоличност=САМОЛИЧНОСТ_ЦЕЛ,
        активни_форми={"F001": 1},
    )
    with pytest.raises(пр.ПредложениетоНеЕПриложимо):
        предложение.за_приемане()


def test_belezhkata_i_handle_at_na_donora_ne_se_prenasyat(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0],
    )
    предложение = пр.Указател.от_проект(донор).предложи(
        _ред_без_кандидат(),
        самоличност=САМОЛИЧНОСТ_ЦЕЛ,
        активни_форми={"F001": 1},
    )
    стойности = предложение.за_приемане(изрично_прието=True)
    сурово = json.dumps(стойности, ensure_ascii=False)
    assert "тестова бележка на донора" not in сурово
    assert стойности["requires_fresh_note"] is True
    assert "geometry_handles" not in стойности
    assert "beam" not in сурово.lower()


# ---------------------------------------------------------------------------
# Оттеглянето
# ---------------------------------------------------------------------------
def test_otteglyanoto_reshenie_ne_predlaga_poveche(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    плик = _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0],
    )
    преди = пр.Указател.от_проект(донор)
    assert преди.предложи(
        _ред_без_кандидат(), самоличност=САМОЛИЧНОСТ_ЦЕЛ,
        активни_форми={"F001": 1},
    ).състояние == пр.ПРИЛОЖИМО

    withdraw_project_decision(
        донор, [плик["event_id"]],
        actor="Тестов оператор", reason="сгрешена форма",
    )
    след = пр.Указател.от_проект(донор)
    предложение = след.предложи(
        _ред_без_кандидат(), самоличност=САМОЛИЧНОСТ_ЦЕЛ,
        активни_форми={"F001": 1},
    )
    assert предложение.състояние == пр.НЕНАМЕРЕНО
    assert след.сводка.отхвърлени.get("ОТТЕГЛЕНО") == 1
    # ИСТОРИЯТА НЕ Е ИЗТРИТА: и решението, и оттеглянето са в журнала.
    журнал = (донор / "working" / JOURNAL_NAME).read_text(encoding="utf-8")
    assert плик["event_id"] in журнал
    assert "ENGINEER_DECISION_WITHDRAWN" in журнал
    оттеглени = withdrawn_decision_ids(донор)
    assert оттеглени[плик["event_id"]]["reason"] == "сгрешена форма"
    assert оттеглени[плик["event_id"]]["actor"]


def test_ostaryal_dialog_ne_mozhe_da_prieme_otteglenoto(tmp_path):
    """Предложението, заснето преди оттеглянето, вече не е в сила."""

    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    плик = _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0],
    )
    стар_указател = пр.Указател.от_проект(донор)
    заснето = стар_указател.предложи(
        _ред_без_кандидат(), самоличност=САМОЛИЧНОСТ_ЦЕЛ,
        активни_форми={"F001": 1},
    )
    assert заснето.може_да_се_приеме is True

    withdraw_project_decision(
        донор, [плик["event_id"]], actor="Оператор", reason="грешка",
    )
    нов_указател = пр.Указател.от_проект(донор)
    отпаднали = нов_указател.отпаднало_основание(заснето.основание())
    assert отпаднали == (плик["event_id"],)
    # Старият указател не знае за оттеглянето — затова диалогът пита НОВИЯ.
    assert стар_указател.отпаднало_основание(заснето.основание()) == ()


def test_otteglyane_bez_motiv_i_bez_avtor_se_otkazva(tmp_path):
    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    плик = _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0],
    )
    with pytest.raises(DecisionWithdrawError):
        withdraw_project_decision(
            донор, [плик["event_id"]], actor="", reason="мотив",
        )
    with pytest.raises(DecisionWithdrawError):
        withdraw_project_decision(
            донор, [плик["event_id"]], actor="Оператор", reason="",
        )
    with pytest.raises(DecisionWithdrawError):
        withdraw_project_decision(
            донор, ["нямаго"], actor="Оператор", reason="мотив",
        )
    assert пр.Указател.от_проект(донор).всички()


def test_reshenie_ne_otmenya_chuzhdo_reshenie_sas_sashtiya_handle(tmp_path):
    """``handle`` е самоличност В ЕДИН DXF, не между чертежи.

    Измерено: ``s-8`` и ``s-9`` носят един и същ ``81E9::BJ-FI-L``. Дотук
    отмяната се смяташе САМО по handle, затова решение в целевия проект
    обезсилваше ДОНОРСКОТО решение, щом двете се окажат в едно досие — и
    приетото предложение веднага обявяваше основанието си за отпаднало.
    """

    проект = _проект(tmp_path / "cel", САМОЛИЧНОСТ_ЦЕЛ)
    чуждо = _решение(
        проект, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], handle="81E9::BJ-FI-L",
        самоличност=САМОЛИЧНОСТ_ДОНОР,
    )
    свое = _решение(
        проект, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], handle="81E9::BJ-FI-L",
        самоличност=САМОЛИЧНОСТ_ЦЕЛ,
        допълнително={
            "preizpolzvane": {
                "схема": "lion-rebar-preizpolzvane-ui-odit/v1",
                "изрично_прието": True,
                "основание": [чуждо["event_id"]],
            }
        },
    )
    assert свое["supersedes_event_ids"] == []
    assert свое["decision_revision"] == 1
    # Основанието НЕ е отпаднало само защото приемането е записано.
    assert пр.зависими_за_повторна_проверка(проект) == []

    # А решение върху СЪЩИЯ чертеж пак отменя предишното.
    трето = _решение(
        проект, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], handle="81E9::BJ-FI-L",
        самоличност=САМОЛИЧНОСТ_ЦЕЛ, причина="WRONG_SHAPE",
        действие="CORRECTION", геометрия=["AAA1"],
    )
    assert трето["supersedes_event_ids"] == [свое["event_id"]]
    assert трето["decision_revision"] == 2


def test_zavisimite_prieti_rezultati_se_markirat(tmp_path):
    """Приет резултат, чието основание е оттеглено, се изброява поименно."""

    проект = _проект(tmp_path / "cel", САМОЛИЧНОСТ_ЦЕЛ)
    донорски = _решение(
        проект, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], handle="AAAA::BJ-FI-L",
        самоличност=САМОЛИЧНОСТ_ЦЕЛ,
    )
    прието = _решение(
        проект, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], handle="BBBB::BJ-FI-L",
        самоличност=САМОЛИЧНОСТ_ЦЕЛ,
        допълнително={
            "preizpolzvane": {
                "схема": "lion-rebar-preizpolzvane-ui-odit/v1",
                "изрично_прието": True,
                "основание": [донорски["event_id"]],
            }
        },
    )
    assert пр.зависими_за_повторна_проверка(проект) == []

    withdraw_project_decision(
        проект, [донорски["event_id"]],
        actor="Оператор", reason="донорът беше грешен",
    )
    зависими = пр.зависими_за_повторна_проверка(проект)
    assert len(зависими) == 1
    assert зависими[0]["event_id"] == прието["event_id"]
    assert зависими[0]["отпаднало_основание"] == [донорски["event_id"]]
    assert зависими[0]["причина"] == "ОСНОВАНИЕТО_Е_ОТТЕГЛЕНО"
    assert зависими[0]["оттеглянето"][0]["мотив"] == "донорът беше грешен"


# ---------------------------------------------------------------------------
# Проходът в анализа не пипа производствените полета
# ---------------------------------------------------------------------------
def test_prohodat_v_analiza_ne_pipa_proizvodstvenite_poleta(tmp_path):
    from lion_rebar.dxf.batch_geometry_report import (
        _apply_decision_reuse_proposals,
    )

    донор = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    _решение(
        донор, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0],
    )

    class _Форма:
        code = "F001"
        revision = 1
        active = True

    ред = _ред_без_кандидат()
    преди = json.dumps(ред, ensure_ascii=False, sort_keys=True)
    редове = [ред]
    сметка = _apply_decision_reuse_proposals(
        редове,
        reuse=пр.Указател.от_проект(донор),
        data_identity=САМОЛИЧНОСТ_ЦЕЛ,
        shape_definitions=[_Форма()],
    )
    assert сметка.get(пр.ПРИЛОЖИМО) == 1
    запис = редове[0].pop("decision_reuse")
    бележки = редове[0].pop("candidate_notes")
    assert запис["състояние"] == пр.ПРИЛОЖИМО
    assert запис["може_да_се_приеме"] is True
    assert any("ПРЕИЗПОЛЗВАНЕ" in текст for текст in бележки)
    # Всичко останало на реда е същото — нито едно производствено поле.
    assert json.dumps(редове[0], ensure_ascii=False, sort_keys=True) == преди


def test_prohodat_bez_ukazatel_ne_pravi_nishto():
    from lion_rebar.dxf.batch_geometry_report import (
        _apply_decision_reuse_proposals,
    )

    ред = _ред_без_кандидат()
    преди = json.dumps(ред, ensure_ascii=False, sort_keys=True)
    assert _apply_decision_reuse_proposals(
        [ред], reuse=None, data_identity=САМОЛИЧНОСТ_ЦЕЛ,
    ) == {}
    assert json.dumps(ред, ensure_ascii=False, sort_keys=True) == преди


def test_rabotnikat_podava_ukazatelya_na_analiza(tmp_path, monkeypatch):
    """ЕДИНСТВЕНАТА точка, през която минават и двата производствени пътя.

    И прозорецът (``ui/main_window``), и безинтерфейсният
    ``.claude/skills/lion-rebar-full-path/full_path.py`` вървят през
    ``BatchGeometryWorker``. Затова проверката тук покрива и двата — същата
    точка, в която разминаването ``version`` / ``engine_version`` беше
    оставило механизма мъртъв.
    """

    from lion_rebar.ui import batch_geometry_worker as модул

    проект = _проект(tmp_path / "proekt", САМОЛИЧНОСТ_ЦЕЛ)
    _решение(
        проект, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], самоличност=САМОЛИЧНОСТ_ЦЕЛ,
    )

    подадено: dict = {}

    def _прихвани(*аргументи, **ключови):
        подадено.update(ключови)
        raise RuntimeError("спряно след проверката на входа")

    monkeypatch.setattr(модул, "build_batch_geometry_report", _прихвани)
    monkeypatch.delenv("LION_REBAR_DECISION_MEMORY", raising=False)

    class _Проект:
        root = str(проект)
        name = "proekt"

    работник = модул.BatchGeometryWorker(
        entities=[],
        output_parent=tmp_path / "out",
        project_name="proekt",
        approved_links={},
        shape_definitions=[],
        control_tests=[],
        policy=None,
        engine_metadata={"engine_id": "e", "engine_version": "0.3.15"},
        project=_Проект(),
        data_identity=САМОЛИЧНОСТ_ЦЕЛ,
    )
    работник.run()
    assert "decision_reuse" in подадено
    указател = подадено["decision_reuse"]
    assert указател is not None
    assert len(указател.всички()) == 1
    assert подадено["engine_metadata"]["engine_version"] == "0.3.15"


def test_otteglyaneto_spira_i_stariya_strog_ukazatel(tmp_path):
    """Оттеглянето важи и за ``decision_suggestions``, не само за новия път.

    Доказано червено срещу старото поведение: без
    ``ENGINEER_DECISION_WITHDRAWN`` в ``DECISION_EVENT_TYPES`` пликът на
    оттеглянето не влиза в жизнения цикъл на стария указател и той продължава
    да предлага оттегленото решение.
    """

    from lion_rebar.review.decision_suggestions import (
        READY,
        DecisionSuggestionIndex,
    )

    проект = _проект(tmp_path / "donor", САМОЛИЧНОСТ_ДОНОР)
    контекст = {
        "annotation_handle": "81E9::BJ-FI-L",
        "annotation_text": "8N8x435",
        "annotation_count": 8,
        "diameter_mm": 8.0,
        "declared_length_cm": 435.0,
        "master_shape_code": "F006",
        "canonical_segment_lengths_cm": [15.0, 405.0, 15.0],
        "candidate_handles": ["14730"],
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "matcher_disposition": "MANUAL",
    }
    товар = {
        "annotation_handle": "81E9::BJ-FI-L",
        "annotation_text": "8N8x435",
        "action_class": "CORRECTION",
        "reason_code": "WRONG_SHAPE",
        "reason": "тест",
        "note": "бележка",
        "actor": "Оператор",
        "data_identity": САМОЛИЧНОСТ_ДОНОР,
        "row_context": dict(контекст),
        "original": {
            "count": 8,
            "diameter_mm": 8.0,
            "declared_length_cm": 435.0,
            "shape_code": "F006",
            "segment_lengths_cm": [15.0, 405.0, 15.0],
            "geometry_handles": ["14730"],
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        },
        "resolved": {
            "count": 8,
            "diameter_mm": 8.0,
            "production_length_cm": 435.0,
            "shape_code": "F006",
            "shape_name": "F006",
            "shape_revision": 1,
            "segment_lengths_cm": [20.0, 395.0, 20.0],
            "turn_angles_deg": [],
        },
        "geometry": {"handles": ["14730"]},
    }
    плик = append_project_decision_event(проект, "ENGINEER_RESOLVED", товар)
    mark_project_decision_delivered(
        проект, плик["event_id"], session_id="S-TEST"
    )
    цел = dict(контекст)
    цел["annotation_handle"] = "CCCC::BJ-FI-L"
    преди = DecisionSuggestionIndex.from_sources(project_root=проект).lookup(
        цел, identity=САМОЛИЧНОСТ_ДОНОР, engine_version="0.3.15",
    )
    assert преди.status == READY

    withdraw_project_decision(
        проект, [плик["event_id"]], actor="Оператор", reason="сгрешено",
    )
    след = DecisionSuggestionIndex.from_sources(project_root=проект).lookup(
        цел, identity=САМОЛИЧНОСТ_ДОНОР, engine_version="0.3.15",
    )
    assert след.status != READY


def test_finalniyat_kontrol_spira_prieto_s_ottegleno_osnovanie(tmp_path):
    """Приетото предложение без основание СПИРА Финалния контрол."""

    from test_recovery_6_3_1_data_integrity import _current_project

    from lion_rebar.project.integrity import reconcile_project_integrity

    _manager, project, _identity, _entities, report_path = _current_project(
        tmp_path
    )
    корен = Path(project.root)
    чисто = reconcile_project_integrity(
        корен, report_path=report_path, apply=False
    )
    assert чисто.integrity_passed is True

    самоличност = {
        "project_id": str(_identity.project_id),
        "source_sha256": str(_identity.source_sha256),
        "source_generation_id": str(_identity.source_generation_id),
        "analysis_run_id": str(_identity.analysis_run_id),
    }
    донорски = _решение(
        корен, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], handle="DONOR-1",
        самоличност=самоличност,
    )
    _решение(
        корен, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], handle="PRIETO-1",
        самоличност=самоличност,
        допълнително={
            "preizpolzvane": {
                "схема": "lion-rebar-preizpolzvane-ui-odit/v1",
                "изрично_прието": True,
                "основание": [донорски["event_id"]],
            }
        },
    )
    още_чисто = reconcile_project_integrity(
        корен, report_path=report_path, apply=False
    )
    assert още_чисто.integrity_passed is True

    withdraw_project_decision(
        корен, [донорски["event_id"]],
        actor="Оператор", reason="донорът беше грешен",
    )
    спряно = reconcile_project_integrity(
        корен, report_path=report_path, apply=False
    )
    assert спряно.integrity_passed is False
    кодове = [issue.code for issue in спряно.issues]
    assert "REUSED_DECISION_BASIS_WITHDRAWN" in кодове
    засегнато = [
        issue for issue in спряно.issues
        if issue.code == "REUSED_DECISION_BASIS_WITHDRAWN"
    ]
    assert засегнато[0].annotation_handles == ["PRIETO-1"]
    assert засегнато[0].details["withdrawn_basis"] == [донорски["event_id"]]


def test_zapisaniyat_kontrolen_klyuch_izklyuchva_preizpolzvaneto(
    tmp_path, monkeypatch,
):
    from lion_rebar.ui import batch_geometry_worker as модул

    проект = _проект(tmp_path / "proekt", САМОЛИЧНОСТ_ЦЕЛ)
    _решение(
        проект, надпис="350N12x300", бройка=350, диаметър=12.0, обявена=300.0,
        форма="F001", участъци=[300.0], самоличност=САМОЛИЧНОСТ_ЦЕЛ,
    )
    подадено: dict = {}

    def _прихвани(*аргументи, **ключови):
        подадено.update(ключови)
        raise RuntimeError("спряно след проверката на входа")

    monkeypatch.setattr(модул, "build_batch_geometry_report", _прихвани)
    monkeypatch.setenv("LION_REBAR_DECISION_MEMORY", "off")

    class _Проект:
        root = str(проект)
        name = "proekt"

    работник = модул.BatchGeometryWorker(
        entities=[],
        output_parent=tmp_path / "out",
        project_name="proekt",
        approved_links={},
        shape_definitions=[],
        control_tests=[],
        policy=None,
        engine_metadata={"engine_id": "e", "engine_version": "0.3.15"},
        project=_Проект(),
        data_identity=САМОЛИЧНОСТ_ЦЕЛ,
    )
    работник.run()
    assert подадено["decision_reuse"] is None
    assert подадено["decision_memory"] is None
