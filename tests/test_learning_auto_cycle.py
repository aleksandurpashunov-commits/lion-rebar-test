"""Автоматичният цикъл и обновяването от външни източници.

Какво пазят тези тестове:

* наблюдението се записва ЕДНО на семейство — ревизия заменя, не се удвоява;
* новото знание важи от СЛЕДВАЩИЯ анализ, не върху чертежа, който го роди;
* неразпознатите случаи се записват с причината си;
* външното знание е винаги UNCONFIRMED и никога не става правило само заради
  източника;
* производствено правило без име на стандарт се отказва (изискване 6);
* без интернет и без файл програмата продължава с наличната памет.
"""
from __future__ import annotations

import json

import pytest

from lion_rebar.learning.auto_cycle import (
    load_observations,
    observations_path,
    record_observation,
    run_cycle,
    unrecognised_from,
)
from lion_rebar.learning.external_update import (
    EXTERNAL_SCHEMA,
    ExternalEntry,
    external_path,
    load_external,
    save_external,
    update,
)
from lion_rebar.learning.knowledge_store import ACCEPTED, KnowledgeStore


#: Общ диалект (14 образеца) за наблюденията тук: така независимите източници са
#: в ЕДНО стилово семейство и приетото е СЕМЕЙНА конвенция.
_ДИАЛЕКТ = tuple("образец %02d #N#" % и for и in range(1, 15))


def _подпис(източник: str) -> dict:
    """Подпис по съдържание с РАЗЛИЧНИ надписи — доказано различен източник."""

    from lion_rebar.learning.source_identity import signature_from_labels

    return signature_from_labels(
        ["%s надпис %02d" % (източник, и) for и in range(1, 21)],
        patterns=_ДИАЛЕКТ)


def _профил(слой: str, *, предложения=True, брой: int = 4) -> dict:
    предл = [{"target_kind": "LAYER", "layer": слой, "role": "ZONE_TITLE",
              "confidence": 0.9, "evidence": "проба", "sample_handles": ["H1"],
              "counts": {"zone_titles": брой}, "details": {}}] if предложения else []
    откази = [] if предложения else [
        {"target_kind": "LAYER", "layer": слой, "role": "ZONE_TITLE",
         "reason": "само 2 надписа изглеждат като заглавие — под 3 не се предлага",
         "counts": {"zone_titles": 2}}]
    return {"proposals": предл, "refusals": откази,
            "summary": {"proposal_count": len(предл), "refusal_count": len(откази)}}


# --- наблюдения -------------------------------------------------------------


def test_edno_nablyudenie_na_semeystvo(tmp_path) -> None:
    п = tmp_path / "nablyudeniya.jsonl"
    record_observation(п, drawing="а", family="СЕМ", profile=_профил("L1"),
                       layer_roles={})
    record_observation(п, drawing="а-ревизия", family="СЕМ", profile=_профил("L2"),
                       layer_roles={})
    record_observation(п, drawing="б", family="ДРУГО", profile=_профил("L3"),
                       layer_roles={})
    наблюдения = load_observations(п)
    семейства = [н["family"] for н in наблюдения]
    assert семейства == ["ДРУГО", "СЕМ"] or семейства == ["СЕМ", "ДРУГО"]
    assert len(наблюдения) == 2, "ревизията заменя, не се добавя"
    по_сем = {н["family"]: н["drawing"] for н in наблюдения}
    assert по_сем["СЕМ"] == "а-ревизия"


def test_nerazpoznatite_se_zapisvat_s_prichina() -> None:
    профил = _профил("Layer9", предложения=False)
    приложено = {"resolved": [], "still_refused": [
        {"layer": "Layer9", "role": "ZONE_TITLE",
         "profile_reason": "само 2 надписа",
         "learned_refusal": "няма прието правило"}]}
    редове = unrecognised_from(профил, приложено)
    assert редове
    assert any(р.get("learned_refusal") for р in редове)


# --- цикълът ----------------------------------------------------------------


def test_naucheno_vazhi_ot_sledvashtiya_analiz(tmp_path) -> None:
    """Чертежът, който ражда правилото, не го ползва в същия прогон.

    Трите наблюдения носят РАЗЛИЧНИ подписи по съдържание (покритие 0), затова са
    три независими източника. Преди поправката от 30.09.2026 тук стигаха три
    различни ИМЕНА — точно това правеше два файла на един чертеж две
    доказателства.
    """

    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    # три ДОКАЗАНО различни източника -> правилото трябва да се приеме
    for чертеж, семейство, слой in (("а", "А", "Alpha"), ("б", "Б", "Beta"),
                                    ("в", "В", "Gamma")):
        резултат = run_cycle(
            store=store, profile=_профил(слой), layer_roles={},
            applied={"resolved": [], "authorized": [], "still_refused": []},
            drawing=чертеж, family=семейство,
            content_signature=_подпис(чертеж),
        )
    # след третия чертеж има прието правило
    assert store.accepted(), "трите независими източника трябва да родят правило"
    # но приложеното В ТОЗИ прогон е празно — идва от хранилището отпреди цикъла
    assert резултат.applied_rules == ()
    assert резултат.observations_total == 3
    assert резултат.independent_sources == 3


def test_ciklt_zapisva_hranilishteto_i_broya(tmp_path) -> None:
    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    резултат = run_cycle(
        store=store, profile=_профил("Alpha"), layer_roles={},
        applied={"resolved": [], "authorized": [], "still_refused": []},
        drawing="а", family="А")
    assert observations_path(store).is_file()
    assert резултат.observations_total == 1
    наново = KnowledgeStore.load(store.path)
    assert наново.meta.get("auto_cycle_drawing") == "а"


def test_zaklyucheno_semeystvo_ne_uchi_i_v_cikala(tmp_path) -> None:
    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    for чертеж, семейство, слой in (("а", "А", "Alpha"), ("б", "Б", "Beta"),
                                    ("з", "ЗАКЛ", "Gamma")):
        run_cycle(store=store, profile=_профил(слой), layer_roles={},
                  applied={"resolved": [], "authorized": [], "still_refused": []},
                  drawing=чертеж, family=семейство,
                  content_signature=_подпис(чертеж),
                  locked_families=("ЗАКЛ",))
    приети = [п for п in store.accepted()
              if п.signature.get("match_mode") == "CONTENT_ONLY"]
    # само А и Б броят -> две семейства, а за „по съдържание" трябват три
    assert приети == []


# --- външно знание ----------------------------------------------------------


def _запис(**промени) -> ExternalEntry:
    данни = {"entry_id": "E1", "kind": "convention", "title": "проба",
             "statement": "нещо", "source_id": "S01", "url": "https://пример",
             "licence": "CC BY 4.0", "accessed": "2026-09-27"}
    данни.update(промени)
    return ExternalEntry(**данни)


def test_vunshnoto_znanie_e_nepotvurdeno_po_podrazbirane(tmp_path) -> None:
    save_external(tmp_path, [_запис()])
    записи = load_external(tmp_path)
    assert len(записи) == 1
    assert записи[0].state == "UNCONFIRMED"


def test_proizvodstveno_pravilo_bez_standart_se_otkazva(tmp_path) -> None:
    with pytest.raises(ValueError, match="без име на стандарт"):
        save_external(tmp_path, [_запис(kind="production", standard="")])
    # със стандарт минава
    път = save_external(tmp_path, [_запис(kind="production", standard="BS 8666:2020")])
    assert път.is_file()


def test_nepoznat_vid_znanie_se_otkazva(tmp_path) -> None:
    with pytest.raises(ValueError, match="непознат вид"):
        save_external(tmp_path, [_запис(kind="измислен")])


def test_chuzhda_shema_na_vunshnoto_znanie_se_otkazva(tmp_path) -> None:
    external_path(tmp_path).write_text(
        json.dumps({"schema": "друго/v1", "entries": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="непозната схема"):
        load_external(tmp_path)


def test_bez_internet_obnovyavaneto_ne_chupi(tmp_path) -> None:
    (tmp_path / "izvori.json").write_text(json.dumps({"sources": [
        {"id": "S01", "url": "https://example.invalid/a.json", "host": "example.invalid"}
    ]}), encoding="utf-8")
    отчет = update(tmp_path, offline=True)
    assert отчет["offline"] is True
    assert отчет["fetch"][0]["status"] == "SKIPPED"
    assert отчет["limits"]["max_sources_per_run"] >= 1


def test_izvor_izvun_spisuka_se_propuska(tmp_path) -> None:
    (tmp_path / "izvori.json").write_text(json.dumps({"sources": [
        {"id": "S01", "url": "https://зъл.example/a.json", "host": "dobur.example"}
    ]}), encoding="utf-8")
    отчет = update(tmp_path, offline=False)
    assert отчет["fetch"][0]["status"] == "SKIPPED"
    assert "не е в списъка" in отчет["fetch"][0]["detail"]


def test_bez_registur_obnovyavaneto_e_prazno(tmp_path) -> None:
    отчет = update(tmp_path, offline=True)
    assert отчет["sources_seen"] == 0
    assert отчет["fetch"] == []


def test_edno_i_sushto_sudurzhanie_pod_dve_imena_NE_e_dve_dokazatelstva(tmp_path) -> None:
    """Самоличността е SHA-256 на изходния DXF, не името на проекта.

    Дефект, намерен на етап 5: ``record_observation`` приемаше ``source_sha256``,
    но никой не го подаваше. Дублирането се пазеше само по ИМЕ на семейство,
    тоест един и същ DXF, внесен под две имена, изпълняваше сам изискването
    „доказано в три семейства".
    """

    from lion_rebar.learning.auto_cycle import load_observations, record_observation

    път = tmp_path / "nablyudeniya.jsonl"
    профил = {"proposals": [], "refusals": [], "summary": {}}
    сха = "a" * 64

    record_observation(път, drawing="проект-А", family="проект-А",
                       profile=профил, layer_roles={}, source_sha256=сха)
    брой = record_observation(път, drawing="копие-под-друго-име",
                              family="копие-под-друго-име",
                              profile=профил, layer_roles={}, source_sha256=сха)

    assert брой == 1, "копие под друго име НЕ е второ доказателство"
    записи = load_observations(път)
    assert [з["family"] for з in записи] == ["копие-под-друго-име"]


def test_druga_revizia_na_sushtiya_proekt_zamenya_predishnata(tmp_path) -> None:
    """Ревизия (друг sha, същото семейство) заменя, а не се събира."""

    from lion_rebar.learning.auto_cycle import load_observations, record_observation

    път = tmp_path / "nablyudeniya.jsonl"
    профил = {"proposals": [], "refusals": [], "summary": {}}
    record_observation(път, drawing="п", family="п", profile=профил,
                       layer_roles={}, source_sha256="b" * 64)
    брой = record_observation(път, drawing="п", family="п", profile=профил,
                              layer_roles={}, source_sha256="c" * 64)
    assert брой == 1
    assert load_observations(път)[0]["source_sha256"] == "c" * 64


def test_razlichno_sudurzhanie_v_razlichni_semeystva_sa_dve(tmp_path) -> None:
    """Истинските два примера си остават два."""

    from lion_rebar.learning.auto_cycle import record_observation

    път = tmp_path / "nablyudeniya.jsonl"
    профил = {"proposals": [], "refusals": [], "summary": {}}
    record_observation(път, drawing="А", family="А", profile=профил,
                       layer_roles={}, source_sha256="d" * 64)
    брой = record_observation(път, drawing="Б", family="Б", profile=профил,
                              layer_roles={}, source_sha256="e" * 64)
    assert брой == 2


def test_bez_sha_povedenieto_ostava_kakvoto_beshe(tmp_path) -> None:
    """Празен sha не слепва наблюдения — старите записи нямат такъв."""

    from lion_rebar.learning.auto_cycle import record_observation

    път = tmp_path / "nablyudeniya.jsonl"
    профил = {"proposals": [], "refusals": [], "summary": {}}
    record_observation(път, drawing="А", family="А", profile=профил,
                       layer_roles={}, source_sha256="")
    брой = record_observation(път, drawing="Б", family="Б", profile=профил,
                              layer_roles={}, source_sha256="")
    assert брой == 2
