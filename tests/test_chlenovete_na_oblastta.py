# -*- coding: utf-8 -*-
"""Всеки член на груповото решение за площ минава СВОЯ проверка (§ 6.1).

Задание от 30.09.2026, § 6.1, и уговорка на собственика от 01.10.2026:
„всяка позиция в групата да преминава собствена проверка, а несъвместимите
членове да остават за преглед с конкретна причина."

ДЕЛТАТА, която този файл пази. Рамката с два клика е групово решение: „всичко в
този правоъгълник е на тази греда". Преценката се правеше, но се ИЗХВЪРЛЯШЕ —
операторът виждаше броя и най-много осем премествания, а записът на областта не
казваше нито една позиция. След записа не можеше да се провери НИТО ЕДИН член.

И два изхода бяха мълчаливи: обект без точка на вмъкване (не може да се реши
дали е вътре) и обект в рамката, който не е позиция. Сега и двата имат присъда
с причина.

Какво НЕ се твърди тук: че преместването от друга греда е забранено. Рамката
бие автоматиката по изрично решение на собственика от 2026-09-14. Новото е, че
преместването е ЗАПИСАНО поименно, с коя греда е било и с показателите на
автоматиката — за да може да се провери след това.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.operator_area_members import (
    БЕЗ_ТОЧКА,
    НЕ_Е_ПОЗИЦИЯ,
    ПОТВЪРДЕН_ДРУГАДЕ,
    ПРЕМЕСТЕН,
    ПРИЕТ,
    area_member_verdicts,
    members_from_dicts,
    ordered_for_review,
    summarise,
)
from lion_rebar.export.operator_areas import (
    AREAS_NAME,
    KIND_BEAM,
    KIND_GROUP,
    load_operator_areas,
    record_operator_area,
)
from lion_rebar.project.data_identity import DataIdentity

СТЪПКА = 400.0


def _текст(handle, text, x, y, layer="ARMTXT", тип="BLOCK_ATTR_TEXT"):
    return DxfEntityRecord(
        handle=handle,
        entity_type=тип,
        layer=layer,
        text=text,
        geometry={"insert": [x, y, 0.0], "height": 10.0},
    )


def _без_точка(handle, text, layer="ARMTXT", тип="BLOCK_ATTR_TEXT"):
    """Истинска позиция, на която DXF-ът не дава точка на вмъкване."""

    return DxfEntityRecord(
        handle=handle, entity_type=тип, layer=layer, text=text, geometry={}
    )


def _чертеж(*допълнителни):
    обекти = [
        _текст("F1", "ЧЕРТЕЖ:", 0.0, 2000.0, layer="ramka", тип="TEXT"),
        _текст("F2", "АРМИРОВКА ГРЕДИ НА КОТА +6.50", 200.0, 2000.0,
               layer="ramka", тип="TEXT"),
        _текст("B1", "Гр. A.1 25/50", 100.0, 1000.0, layer="Section", тип="TEXT"),
        _текст("B2", "Гр. A.2 25/50", 100.0, 1000.0 - СТЪПКА,
               layer="Section", тип="TEXT"),
        _текст("P1", "4N12x240", 300.0, 1000.0 - 0.745 * СТЪПКА),
        _текст("P2", "6N14x310", 300.0, 1000.0 - СТЪПКА - 0.745 * СТЪПКА),
        _текст("N2", "стр. 59N8/s=20", 260.0, 1000.0 - СТЪПКА + 20.0,
               layer="Section", тип="TEXT"),
    ]
    обекти.extend(допълнителни)
    return обекти


def _рамка():
    return (50.0, 1000.0 - СТЪПКА - 0.9 * СТЪПКА, 400.0, 1000.0 - СТЪПКА + 60.0)


def _широка_рамка():
    x0, y0, x1, _y1 = _рамка()
    return (x0, y0, x1, 1000.0)


def _присъди(обекти, box, *, kind=KIND_BEAM, title_handle="B2", bent_around=(),
             потвърдени=None):
    return area_member_verdicts(
        entities=обекти,
        policy=None,
        box=box,
        bent_around=bent_around,
        kind=kind,
        title_handle=title_handle,
        confirmed_beams=потвърдени,
    )


# --- всеки член получава присъда с причина ---------------------------------


def test_vseki_obekt_v_ramkata_poluchava_prisada():
    членове = _присъди(_чертеж(), _рамка())

    по_handle = {член.handle: член for член in членове}
    # Позицията на тази греда е приета.
    assert по_handle["P2"].verdict == ПРИЕТ
    # Бележката за стремената е в рамката, но НЕ е позиция — и това се казва.
    assert по_handle["N2"].verdict == НЕ_Е_ПОЗИЦИЯ
    assert "не е позиция" in по_handle["N2"].reason
    # Всяка присъда носи причина, не само код.
    assert all(член.reason for член in членове)


def test_pozicia_bez_tochka_ostava_za_pregled_s_prichina():
    """Дотук се пропускаше мълчаливо: позицията изчезваше без следа."""

    членове = _присъди(_чертеж(_без_точка("P9", "8N10x150")), _рамка())

    по_handle = {член.handle: член for член in членове}
    assert по_handle["P9"].verdict == БЕЗ_ТОЧКА
    assert "не може да се реши" in по_handle["P9"].reason
    # И не се брои за приет член.
    assert summarise(членове)["for_review"] == 1


def test_ne_pozitsia_bez_tochka_ne_vdiga_shum():
    """Обект без точка, който и без това не е позиция, не занимава оператора."""

    членове = _присъди(_чертеж(_без_точка("X9", "ЗАБЕЛЕЖКА")), _рамка())

    assert "X9" not in {член.handle for член in членове}


def test_premestvaneto_se_zapisva_poimenno_s_pokazatelite():
    членове = _присъди(_чертеж(), _широка_рамка())

    преместени = [член for член in членове if член.verdict == ПРЕМЕСТЕН]
    assert [член.text for член in преместени] == ["4N12x240"]
    член = преместени[0]
    assert член.from_title == "Гр. A.1 25/50"
    assert член.from_handle == "B1"
    # Показателите на автоматиката влизат в записа: прагът за спиране е
    # решение на собственика върху измерени числа, не догадка на кода.
    assert член.score is not None
    assert член.margin is not None


# --- таблицата на собственика от 02.10.2026, състояние по състояние ---------


def test_sastoyanie_1_nyama_ustanovena_prinadlezhnost():
    """Предложение за принадлежност, показано преди запис."""

    членове = _присъди(_чертеж(), _рамка())

    по_handle = {член.handle: член for член in членове}
    assert по_handle["P2"].verdict == ПРИЕТ
    assert по_handle["P2"].from_title is None


def test_sastoyanie_2_samo_avtomatichno_predpolozhenie():
    """Видимо предупреждение и потвърждение от оператора; рамката печели."""

    членове = _присъди(_чертеж(), _широка_рамка())

    преместен = next(член for член in членове if член.verdict == ПРЕМЕСТЕН)
    assert преместен.text == "4N12x240"
    assert преместен.from_title == "Гр. A.1 25/50"
    assert "автоматиката" in преместен.reason


def test_sastoyanie_3_veche_potvardena_ot_operator_NE_se_mesti():
    """Очертаването на рамка не отменя предишно потвърдено решение.

    Собственик, 02.10.2026. Дотук рамката побеждаваше безусловно — включително
    над `record_beam_decision`, което сочи ЕДНА позиция, с автор и мотив.
    """

    членове = _присъди(
        _чертеж(), _широка_рамка(), потвърдени={"P1": "B1"}
    )

    по_handle = {член.handle: член for член in членове}
    assert по_handle["P1"].verdict == ПОТВЪРДЕН_ДРУГАДЕ
    assert по_handle["P1"].from_title == "Гр. A.1 25/50"
    assert "изрично решение" in по_handle["P1"].reason
    # И НЕ влиза в групата.
    assert по_handle["P1"].verdict not in {ПРИЕТ, ПРЕМЕСТЕН}
    assert summarise(членове)["accepted"] == 1
    assert summarise(членове)["confirmed_elsewhere"] == 1


def test_potvardena_za_sashtata_greda_ne_e_spor():
    """Потвърдена принадлежност към ТАЗИ греда минава нормално."""

    членове = _присъди(_чертеж(), _рамка(), потвърдени={"P2": "B2"})

    по_handle = {член.handle: член for член in членове}
    assert по_handle["P2"].verdict == ПРИЕТ


def test_sastoyanie_4_bez_tochka_ostava_za_pregled():
    """Вече покрито по-горе; тук се пази, че е част от ЗА_ПРЕГЛЕД."""

    from lion_rebar.export.operator_area_members import ЗА_ПРЕГЛЕД

    assert БЕЗ_ТОЧКА in ЗА_ПРЕГЛЕД
    assert ПОТВЪРДЕН_ДРУГАДЕ in ЗА_ПРЕГЛЕД


def test_pokazatelite_samo_podrezhdat_i_nyama_prag():
    """Пръв е случаят, в който автоматиката е била най-уверена."""

    from lion_rebar.export.operator_area_members import ЧленНаОбласт

    членове = [
        ЧленНаОбласт(handle="A", text="A", verdict=ПРЕМЕСТЕН, reason="", margin=0.3, score=1.0),
        ЧленНаОбласт(handle="B", text="B", verdict=ПРЕМЕСТЕН, reason="", margin=0.9, score=2.0),
        ЧленНаОбласт(handle="C", text="C", verdict=ПРИЕТ, reason=""),
        ЧленНаОбласт(handle="D", text="D", verdict=ПРЕМЕСТЕН, reason="", margin=None, score=None),
    ]

    подредени = ordered_for_review(членове)

    assert [член.handle for член in подредени] == ["B", "A", "D"]
    # Нищо не пада заради число: и трите премествания са в списъка.
    assert len(подредени) == 3


def test_svodkata_broi_po_prisada():
    сводка = summarise(_присъди(_чертеж(), _широка_рамка()))

    assert сводка["accepted"] == 2
    assert сводка["moved"] == 1
    assert сводка["for_review"] == 0
    assert сводка["total"] == сводка["accepted"] + сводка["by_verdict"].get(
        НЕ_Е_ПОЗИЦИЯ, 0
    )


def test_grupovata_ramka_ne_pita_za_gredi():
    членове = _присъди(_чертеж(), _рамка(), kind=KIND_GROUP, title_handle="B2")

    assert all(член.verdict != ПРЕМЕСТЕН for член in членове)
    assert any(член.verdict == ПРИЕТ for член in членове)


def test_oganatoto_ne_vliza_v_chlenovete():
    """Позиция на съседна рамка не става член на тази."""

    съседна = (50.0, 1000.0 - СТЪПКА - 0.9 * СТЪПКА, 400.0, 1000.0 - СТЪПКА - 0.5 * СТЪПКА)
    членове = _присъди(_чертеж(), _рамка(), bent_around=(съседна,))

    assert "P2" not in {член.handle for член in членове}


# --- присъдите стигат до записа и се четат обратно --------------------------


def _самоличност():
    return DataIdentity(
        project_id="p-6",
        source_sha256="SHA",
        source_generation_id="G1",
        analysis_run_id="R1",
    )


def test_prisadite_se_zapisvat_s_oblastta_i_se_chetat(tmp_path):
    членове = _присъди(_чертеж(), _широка_рамка())

    record_operator_area(
        tmp_path,
        box=_широка_рамка(),
        kind=KIND_BEAM,
        title_handle="B2",
        title_text="Гр. A.2 25/50",
        frame_index=0,
        decided_by="Александър",
        note="проверка на членовете",
        identity=_самоличност(),
        members=[член.to_dict() for член in членове],
    )

    записано = load_operator_areas(tmp_path, _самоличност())
    assert len(записано) == 1
    прочетени = members_from_dicts(записано[0].members)
    assert len(прочетени) == len(членове)
    assert {член.handle for член in прочетени} == {член.handle for член in членове}
    преместен = next(член for член in прочетени if член.verdict == ПРЕМЕСТЕН)
    assert преместен.from_title == "Гр. A.1 25/50"

    # И на диска, не само в паметта.
    данни = json.loads((tmp_path / "working" / AREAS_NAME).read_text(encoding="utf-8"))
    assert len(данни["areas"][0]["members"]) == len(членове)


def test_polovin_prisada_ne_se_chete(tmp_path):
    """Ред без присъда би минал за приет член — затова пада."""

    record_operator_area(
        tmp_path,
        box=_рамка(),
        kind=KIND_BEAM,
        title_handle="B2",
        title_text="Гр. A.2 25/50",
        frame_index=0,
        decided_by="Александър",
        note="проверка на членовете",
        identity=_самоличност(),
        members=[
            {"handle": "P2", "verdict": ПРИЕТ, "reason": "позиция в рамката"},
            {"handle": "P3"},
            {"verdict": ПРИЕТ},
            "не е речник",
        ],
    )

    записано = load_operator_areas(tmp_path, _самоличност())
    assert [член["handle"] for член in записано[0].members] == ["P2"]


def test_starite_oblasti_bez_chlenove_se_chetat(tmp_path):
    """Записана преди тази промяна област остава валидна, но без членове."""

    работна = tmp_path / "working"
    работна.mkdir(parents=True)
    (работна / AREAS_NAME).write_text(
        json.dumps(
            {
                "schema": "lion-rebar-operator-group-areas/v1",
                "areas": [
                    {
                        "area_id": "старо",
                        "x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0,
                        "kind": KIND_BEAM,
                        "title_handle": "B2",
                        "title_text": "Гр. A.2 25/50",
                        "frame_index": 0,
                        "decided_by": "оператор",
                        "note": "старо",
                        "at": "2026-09-14T10:00:00+00:00",
                        "project_id": "p-6",
                        "source_sha256": "SHA",
                        "source_generation_id": "G1",
                        "analysis_run_id": "R1",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    записано = load_operator_areas(tmp_path, _самоличност())
    assert len(записано) == 1
    # Нито един член не може да бъде проверен — и това си личи.
    assert записано[0].members == ()
