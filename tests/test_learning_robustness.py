"""Устойчивост на научените правила — СИНТЕТИЧНИ проверки.

Изрично: тези случаи са направени на ръка, за да проверят едно свойство всеки.
**Не са независими проекти от нови проектанти** и не доказват полза — доказват
само че механизмът не се чупи и не се разширява там, където не бива.

Покрити случаи от заданието:

* преименуван слой със запазено съдържание;
* познато име на слой с неподходящо съдържание;
* завъртени и преместени форми;
* различни мерни единици;
* дублирани файлове (едно и също съдържание под две имена);
* оттеглено правило;
* повредено хранилище и липсваща папка.
"""
from __future__ import annotations

import json
import math

import pytest

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.learning.convention_apply import apply_learned, authorized_proposals
from lion_rebar.learning.convention_learner import (
    MATCH_CONTENT_ONLY,
    MATCH_NAME_TOKEN,
    learn,
)
from lion_rebar.learning.knowledge_store import (
    ACCEPTED,
    CANDIDATE,
    WITHDRAWN,
    KnowledgeStore,
    LearnedRule,
)
from lion_rebar.learning.spec_layer_profile import full_profile


# --- помощни ---------------------------------------------------------------


def _текст(handle: str, слой: str, текст: str, точка=(0.0, 0.0)) -> DxfEntityRecord:
    return DxfEntityRecord(handle=handle, entity_type="TEXT", layer=слой,
                           text=текст, geometry={"insert": list(точка)})


def _права(handle: str, слой: str, начало, край) -> DxfEntityRecord:
    return DxfEntityRecord(handle=handle, entity_type="LWPOLYLINE", layer=слой,
                           geometry={"points": [list(начало), list(край)]})


def _чертеж(слой_геометрия: str, *, брой: int = 8, дължина: float = 300.0,
            отместване=(0.0, 0.0), ъгъл: float = 0.0,
            мащаб: float = 1.0) -> list[DxfEntityRecord]:
    """Надписи, всеки с прът до себе си с точно надписаната дължина."""

    записи: list[DxfEntityRecord] = []
    for и in range(брой):
        x = и * 500.0
        y = 0.0
        etx, ety = x, y
        bx, by = x, y + дължина
        точки = []
        for px, py in ((etx, ety), (bx, by)):
            px, py = px * мащаб, py * мащаб
            ако = math.radians(ъгъл)
            rx = px * math.cos(ако) - py * math.sin(ако) + отместване[0]
            ry = px * math.sin(ако) + py * math.cos(ако) + отместване[1]
            точки.append((rx, ry))
        записи.append(_права("G%d" % и, слой_геометрия, точки[0], точки[1]))
        tx = точки[0][0] + 5.0
        ty = точки[0][1] + 5.0
        записи.append(_текст("T%d" % и, "ARMTXT_PROBA", "%dN12x%d" % (4 + и, int(дължина)),
                             (tx, ty)))
    return записи


def _наблюдение(чертеж: str, семейство: str, записи, *, роли=None, locked=False):
    return {"drawing": чертеж, "family": семейство, "locked": locked,
            "profile": full_profile(записи), "layer_roles": роли or {}}


def _хранилище_по_съдържание(tmp_path) -> KnowledgeStore:
    """Правило за армировъчна геометрия, доказано при ТРИ РАЗЛИЧНИ имена."""

    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    learn([
        _наблюдение("а", "А", _чертеж("Alpha_Bars")),
        _наблюдение("б", "Б", _чертеж("Beta_Stahl")),
        _наблюдение("в", "В", _чертеж("Gamma_Ferro")),
    ], store=store)
    return store


def _приети_по_роля(store: KnowledgeStore, роля: str, режим: str):
    return [п for п in store.accepted()
            if п.role == роля and п.signature.get("match_mode") == режим]


def _кандидати_по_роля(store: KnowledgeStore, роля: str, режим: str):
    return [п for п in store.by_state(CANDIDATE)
            if п.role == роля and п.signature.get("match_mode") == режим]


# --- преименуване и съдържание --------------------------------------------


def test_preimenuvan_sloy_za_geometriya_ostava_KANDIDAT(tmp_path) -> None:
    """ПРОМЕНЕН тест. Старото изискване е измерено като невярно.

    Обосновка (етап 5, 27.09.2026). Доказателството на режима само по
    съдържание за REBAR_GEOMETRY е: надпис намира до себе си обект с точно
    своята надписана дължина. Това доказателство приема и КОНТУРИ. Измерено на
    d-2 (AutoCAD Structural Detailing, чисто копие, нормални настройки):
    портата пада от 124 на 105 от 178, а анализът от 6,3 s на 128,9 s.
    Блокираните остават 50, но 19 позиции влизат и 19 излизат - размяна, не
    напредък. Приетият слой не е само ...Slab contour: в размяната участват и
    истински слоеве ...Surface reinforcement..., тоест вредата не е в едно име.

    Затова правилото СЕ НАУЧАВА и СЕ ПАЗИ (паметта и доказателството остават),
    но остава КАНДИДАТ и не се прилага автоматично. Свойството, което старият
    тест пазеше - че преименуването не губи доказателството - се пази тук:
    правилото пак се извлича от три различни имена на слой.

    Възстановява се, когато геометричният различител (армировка срещу контур)
    може да разреши режима по съдържание по измерим признак, а не по име.
    """

    store = _хранилище_по_съдържание(tmp_path)
    assert _приети_по_роля(store, "REBAR_GEOMETRY", MATCH_CONTENT_ONLY) == [], (
        "режимът само по съдържание за геометрия не бива да се приема сам")

    кандидати = _кандидати_по_роля(store, "REBAR_GEOMETRY", MATCH_CONTENT_ONLY)
    assert кандидати, "правилото трябва да е НАУЧЕНО, само че неприложено"
    правило = кандидати[0]
    assert правило.families == ("А", "Б", "В"), (
        "доказателството от трите преименувани слоя се пази")
    бележка = json.dumps(правило.validation, ensure_ascii=False)
    assert "изключен" in бележка and "d-2" in бележка, (
        "причината и измерването трябва да са записани в правилото")

    # понеже правилото не е прието, нито един от двата пътя не минава
    профил = full_profile(_чертеж("СъвсемНовоИмеНаСлой", брой=6))
    разрешени = [р for р in authorized_proposals(профил, store=store)
                 if р["role"] == "REBAR_GEOMETRY"]
    assert разрешени == [], "кандидат не разрешава предложение на паспорта"

    малък = full_profile(_чертеж("ОщеЕдноНовоИме", брой=3))
    откази = [о for о in малък.get("refusals") or ()
              if о.get("role") == "REBAR_GEOMETRY"]
    assert откази, "3 съвпадения са под прага на паспорта (5) и трябва да е отказ"
    решени = [р for р in apply_learned(малък, store=store)["resolved"]
              if р["role"] == "REBAR_GEOMETRY"]
    assert решени == [], "кандидат не сваля прага за количество"


def test_poznato_ime_s_nepodhodyashto_sudurzhanie_ne_poluchava_nishto(tmp_path) -> None:
    """Име от обучението, но без нито едно съвпадение по дължина."""

    store = _хранилище_по_съдържание(tmp_path)
    # същото име, но геометрията е далеч и с друга дължина
    записи = [_текст("T1", "ARMTXT_PROBA", "4N12x300", (0.0, 0.0)),
              _права("G1", "Alpha_Bars", (9000.0, 9000.0), (9000.0, 9007.0))]
    профил = full_profile(записи)
    предложени = [п for п in профил.get("proposals") or ()
                  if п.get("role") == "REBAR_GEOMETRY"]
    assert предложени == [], "без съвпадение по дължина няма предложение"
    разрешени = [р for р in authorized_proposals(профил, store=store)
                 if р["role"] == "REBAR_GEOMETRY"]
    assert разрешени == []


def test_imenniyat_klasifikator_spira_sudurzhatelnoto_pravilo(tmp_path) -> None:
    """Слой, който именният класификатор уверено нарича друго."""

    store = _хранилище_по_съдържание(tmp_path)
    профил = full_profile(_чертеж("Ramka_Na_Lista", брой=3))
    роли = {"Ramka_Na_Lista": {"role": "FRAME", "confidence": 0.99}}
    разрешени = [р for р in authorized_proposals(профил, store=store, layer_roles=роли)
                 if р["role"] == "REBAR_GEOMETRY"]
    assert разрешени == []


# --- геометрични преобразувания -------------------------------------------


@pytest.mark.parametrize("ъгъл,отместване", [
    (0.0, (0.0, 0.0)),
    (37.0, (0.0, 0.0)),
    (0.0, (12345.0, -6789.0)),
    (90.0, (500.0, 500.0)),
])
def test_zavurtane_i_premestvane_ne_menyat_prochita(tmp_path, ъгъл, отместване) -> None:
    """Дължината е инвариантна на завъртане и преместване — и прочитът също.

    ПРОМЕНЕН тест: мери се самият прочит (паспортът), не дали наученото
    разрешава слоя. Така свойството се проверява, без да зависи от състоянието
    на паметта - вж. обосновката в
    ``test_preimenuvan_sloy_za_geometriya_ostava_KANDIDAT``. Проверката стана
    по-строга: сравняват се броевете и увереността, а не само че има
    предложение.
    """

    изправен = full_profile(_чертеж("Delta_Rebar", брой=6))
    свои = [п for п in изправен.get("proposals") or ()
            if п.get("role") == "REBAR_GEOMETRY"]
    assert свои, "неотместеният чертеж трябва да дава предложение"

    профил = full_profile(_чертеж("Delta_Rebar", брой=6, ъгъл=ъгъл,
                                  отместване=отместване))
    предложени = [п for п in профил.get("proposals") or ()
                  if п.get("role") == "REBAR_GEOMETRY"]
    assert предложени, "завъртян/преместен чертеж трябва да се чете еднакво"
    assert предложени[0]["counts"] == свои[0]["counts"]
    assert предложени[0]["confidence"] == свои[0]["confidence"]


def test_drugi_edinici_NE_se_priemat_mulchalivo(tmp_path) -> None:
    """Мащабирана геометрия вече не съвпада с надписаната дължина — отказ.

    Това е ЖЕЛАНОТО поведение: правилото мери съвпадение между надпис и обект.
    Ако чертежът е в други единици без нормализация, съвпадение няма и
    програмата мълчи, вместо да обяви слоя за армировъчен.
    """

    store = _хранилище_по_съдържание(tmp_path)
    профил = full_profile(_чертеж("Epsilon_Rebar", брой=6, мащаб=10.0))
    предложени = [п for п in профил.get("proposals") or ()
                  if п.get("role") == "REBAR_GEOMETRY"]
    assert предложени == []
    разрешени = [р for р in authorized_proposals(профил, store=store)
                 if р["role"] == "REBAR_GEOMETRY"]
    assert разрешени == []


# --- дублирани файлове ------------------------------------------------------


def test_dublirani_faylove_ne_sa_dve_dokazatelstva(tmp_path) -> None:
    """Едно и също съдържание под две имена, но в ЕДНО семейство."""

    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    записи = _чертеж("Zeta_Rebar")
    отчет = learn([
        _наблюдение("копие-1", "ЕДНО", записи),
        _наблюдение("копие-2", "ЕДНО", list(записи)),
        _наблюдение("друг", "ДВЕ", _чертеж("Eta_Rebar")),
    ], store=store)
    по_съдържание = [store.get(р["rule_id"]) for р in отчет["accepted"] + отчет["candidates"]
                     if store.get(р["rule_id"]).role == "REBAR_GEOMETRY"
                     and store.get(р["rule_id"]).signature.get("match_mode") == MATCH_CONTENT_ONLY]
    assert по_съдържание
    # две семейства < трите, нужни за режим „само по съдържание"
    assert по_съдържание[0].state != ACCEPTED
    assert по_съдържание[0].families == ("ДВЕ", "ЕДНО")


# --- оттегляне и повреда ----------------------------------------------------


def test_ottegleno_pravilo_ne_se_prilaga(tmp_path) -> None:
    store = _хранилище_по_съдържание(tmp_path)
    # ПРОМЕНЕНО: правилото вече е кандидат, не прието (същата обосновка)
    правило = _кандидати_по_роля(store, "REBAR_GEOMETRY", MATCH_CONTENT_ONLY)[0]
    store.withdraw(правило.rule_id, replaced_by="CR-НОВО")
    store.save()

    наново = KnowledgeStore.load(store.path)
    оттеглено = наново.get(правило.rule_id)
    assert оттеглено is not None and оттеглено.state == WITHDRAWN
    assert оттеглено.validation["replaced_by"] == "CR-НОВО"

    профил = full_profile(_чертеж("Theta_Rebar", брой=4))
    разрешени = [р for р in authorized_proposals(профил, store=наново)
                 if р["role"] == "REBAR_GEOMETRY"]
    assert разрешени == []


def test_povredeno_hranilishte_ne_chupi_a_otkazva(tmp_path) -> None:
    п = tmp_path / "naucheno.json"
    п.write_text("{това не е JSON", encoding="utf-8")
    with pytest.raises(ValueError):
        KnowledgeStore.load(п)


def test_chuzhda_shema_se_otkazva_izricno(tmp_path) -> None:
    п = tmp_path / "naucheno.json"
    п.write_text(json.dumps({"schema": "нещо-друго/v9", "rules": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="непозната схема"):
        KnowledgeStore.load(п)


def test_lipsvashto_hranilishte_e_prazno_a_ne_greshka(tmp_path) -> None:
    """Без интернет и без файл програмата продължава с празна памет."""

    store = KnowledgeStore.load(tmp_path / "няма-я" / "naucheno.json")
    assert len(store) == 0 and store.accepted() == ()
    профил = full_profile(_чертеж("Iota_Rebar", брой=4))
    assert authorized_proposals(профил, store=store) == []
    assert apply_learned(профил, store=store)["resolved"] == []
