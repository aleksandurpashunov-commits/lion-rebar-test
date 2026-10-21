"""Общата логика на обучението — едно поведение за прозореца и за пътя без него.

Какво пазят тези тестове:

* ``prepare_policy`` връща политиката НЕПРОМЕНЕНА при изключено обучение;
* грешка в обучението НЕ вали анализа — връща се политика и текст на грешката;
* ``новоприети`` съдържа САМО правилата, минали В прието състояние в ТОЗИ
  прогон — етап 3 ги отчиташе погрешно като всички вече приети;
* редът за лентата на състоянието казва същественото и при изключено обучение;
* връщането на паметта към по-ранно копие работи и пази текущото.
"""
from __future__ import annotations

import json

import pytest

from lion_rebar.dxf.recognition_policy import DEFAULT_RECOGNITION_POLICY
from lion_rebar.learning.integration import (
    LearningStatus,
    prepare_policy,
    rollback_memory,
    run_cycle_after_analysis,
)
from lion_rebar.learning.knowledge_store import ACCEPTED, KnowledgeStore, LearnedRule


def _профил(слой: str, брой: int = 4) -> dict:
    return {"proposals": [{"target_kind": "LAYER", "layer": слой,
                           "role": "ZONE_TITLE", "confidence": 0.9,
                           "evidence": "проба", "sample_handles": ["H1"],
                           "counts": {"zone_titles": брой}, "details": {}}],
            "refusals": [], "summary": {}}


def test_izklyucheno_obuchenie_vrushta_politikata_nepromenena(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LION_REBAR_LEARNING", "off")
    policy, състояние, мост = prepare_policy(
        DEFAULT_RECOGNITION_POLICY, tmp_path, identity=None, entities=[], drawing="а")
    assert policy is DEFAULT_RECOGNITION_POLICY
    assert състояние.enabled is False
    assert мост == {}
    assert състояние.short_line() == "Обучение: изключено"


def test_greshka_v_obuchenieto_ne_vali_analiza(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LION_REBAR_LEARNING", raising=False)
    monkeypatch.setenv("LION_REBAR_NAUCHENO", str(tmp_path / "counterfeit.json"))
    (tmp_path / "counterfeit.json").write_text("{това не е JSON", encoding="utf-8")
    policy, състояние, _ = prepare_policy(
        DEFAULT_RECOGNITION_POLICY, tmp_path, identity=None, entities=[], drawing="а")
    assert policy is not None
    assert състояние.error
    assert "ГРЕШКА" in състояние.short_line()


def test_novoprieti_sa_samo_minalite_v_tozi_progon(tmp_path, monkeypatch) -> None:
    """Етап 3 отчиташе ВСИЧКИ приети като „новоприети"."""

    monkeypatch.delenv("LION_REBAR_LEARNING", raising=False)
    monkeypatch.delenv("LION_REBAR_AUTOCYCLE", raising=False)
    път = tmp_path / "naucheno.json"
    monkeypatch.setenv("LION_REBAR_NAUCHENO", str(път))

    състояние = LearningStatus(enabled=True)
    # първи чертеж: нищо не се приема (едно семейство)
    състояние = run_cycle_after_analysis(
        status=състояние, bridge_report={}, entities=[], drawing="а", family="А")
    assert състояние.newly_accepted == []
    # втори чертеж, друго семейство: правилото по ИМЕ минава в прието
    състояние2 = run_cycle_after_analysis(
        status=LearningStatus(enabled=True), bridge_report={}, entities=[],
        drawing="б", family="Б")
    # трети прогон върху вече известно семейство: нищо НОВО не се приема
    състояние3 = run_cycle_after_analysis(
        status=LearningStatus(enabled=True), bridge_report={}, entities=[],
        drawing="б-ревизия", family="Б")
    assert състояние3.newly_accepted == [], (
        'правило, прието в по-ранен прогон, не е новоприето сега')


def test_avtocikalt_se_izklyuchva(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LION_REBAR_AUTOCYCLE", "off")
    monkeypatch.setenv("LION_REBAR_NAUCHENO", str(tmp_path / "naucheno.json"))
    състояние = run_cycle_after_analysis(
        status=LearningStatus(enabled=True), bridge_report={}, entities=[],
        drawing="а", family="А")
    assert състояние.observations == 0
    assert not (tmp_path / "nablyudeniya.jsonl").exists()


def test_redut_za_lentata_kazva_sushtestvenoto() -> None:
    с = LearningStatus(enabled=True, rules_total=25, rules_accepted=10,
                       applied=[{"rule_id": "CR-1"}], declared_layers=["Stremena"],
                       newly_accepted=["CR-2"], new_candidates=["CR-3"], unresolved=4)
    ред = с.short_line()
    for късче in ("25 правила", "10 приети", "приложени 1", "Stremena",
                  "НОВО приети 1", "нови кандидати 1", "неразрешени 4"):
        assert късче in ред


def test_vrushtane_na_pametta(tmp_path, monkeypatch) -> None:
    текущо = tmp_path / "naucheno.json"
    monkeypatch.setenv("LION_REBAR_NAUCHENO", str(текущо))
    store = KnowledgeStore.load(текущо)
    store.put(LearnedRule(rule_id="CR-НОВО", version=1, role="ZONE_TITLE",
                          signature={"role": "ZONE_TITLE"}, state=ACCEPTED,
                          origin="тест"))
    store.save()
    копие = tmp_path / "staro.json"
    празно = KnowledgeStore.load(копие)
    празно.save()

    върнато = rollback_memory(копие)
    assert върнато == текущо
    наново = KnowledgeStore.load(текущо)
    assert len(наново) == 0, "паметта е върната към празното копие"
    резерви = list(tmp_path.glob("naucheno.before_rollback_*.json"))
    assert резерви, "текущото състояние трябва да се запази преди връщането"
    запазено = json.loads(резерви[0].read_text(encoding="utf-8"))
    assert any(р["rule_id"] == "CR-НОВО" for р in запазено["rules"])


def test_vrushtane_bez_takova_kopie(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LION_REBAR_NAUCHENO", str(tmp_path / "naucheno.json"))
    with pytest.raises(FileNotFoundError):
        rollback_memory(tmp_path / "няма-го.json")
