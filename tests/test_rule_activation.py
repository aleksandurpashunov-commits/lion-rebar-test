# -*- coding: utf-8 -*-
"""Кога кандидат става ПРИЕТО ПРАВИЛО — и защо дотогава не става.

## Какво се смени на 30.09.2026 и защо

До тази дата `learning_pack` пишеше `activated_rule_count = 0` като **литерал**, а
`test_rc39_learning_stage1.test_pack_reports_candidates_but_activates_no_rule` го
пазеше. Числото оставаше нула независимо от доказателствата, и по-важното: не се
виждаше какво точно липсва на кандидата, за да получи право.

Собственик, 30.09.2026: „Разрешавам активирането на този механизъм в
експерименталната версия. Не го оставяй постоянно изключен чрез
`activated_rule_count = 0`. Запази изключения режим като валидна контролна
конфигурация и актуализирай тестовете с писмено обяснение на новото поведение."

Новото поведение: числото се ИЗЧИСЛЯВА от критериите в
`session/rule_activation.py`. Старият тест продължава да минава — но вече защото
неговият кандидат има ЕДИН свидетел и няма записан одит, а не защото нулата е
вписана в кода. Тези тестове пазят разликата.

## Критериите и защо са точно тези

| критерий | искането на собственика |
|---|---|
| без противоречиви етикети | „еднакви условия не водят до различни решения" |
| ≥ 2 независими свидетеля | „Повторенията сами по себе си не дават това право" |
| определен обхват | „Избирай най-тесния доказан обхват" |
| ≥ 2 семейства за ОБЩО правило | „Отделяй адаптация в семейство от пренос между семейства" |
| записан одит и регресия | „успешно преминаване на независимите проверки" |

`repeat_count` НЕ е критерий — двайсет повторения на един чертеж остават един
свидетел.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from lion_rebar.session import rule_activation as активиране
from lion_rebar.session.learning_pack import build_learning_pack


def _кандидат(**промени) -> dict:
    """Кандидат, който минава ВСИЧКИ критерии; тестът маха по един."""

    основа = {
        "candidate_key": "k1",
        "has_conflicting_labels": False,
        "independent_observation_count": 2,
        "independent_drawing_family_count": 2,
        "project_count": 2,
        "repeat_count": 2,
        "audit_and_regression": {"passed": True, "at": "2026-09-30T10:00:00+03:00"},
    }
    основа.update(промени)
    return основа


def test_pilen_kandidat_poluchava_pravo(monkeypatch):
    """Кандидат с два независими свидетеля, две семейства и минал одит — активиран.

    Това е доказателството, че механизмът НЕ е мъртъв: до 30.09 същият вход
    даваше нула, защото числото беше литерал.
    """

    monkeypatch.delenv("LION_REBAR_RULE_ACTIVATION", raising=False)
    присъда = активиране.присъда(_кандидат())
    assert присъда["activated"] is True
    assert присъда["scope"] == активиране.ОБЩА
    assert присъда["blocked_by"] == []
    assert "право на автоматично прилагане" in присъда["explanation"]


@pytest.mark.parametrize("промяна,спирачка", [
    ({"has_conflicting_labels": True}, "CONFLICT"),
    ({"independent_observation_count": 1, "independent_drawing_family_count": 1,
      "project_count": 1}, "ONE_WITNESS"),
    ({"audit_and_regression": None}, "NO_AUDIT"),
    ({"audit_and_regression": {"passed": False}}, "NO_AUDIT"),
])
def test_vseki_kriteriy_sam_spira(промяна, спирачка, monkeypatch):
    """Всеки критерий поединично отнема правото и се назовава."""

    monkeypatch.delenv("LION_REBAR_RULE_ACTIVATION", raising=False)
    присъда = активиране.присъда(_кандидат(**промяна))
    assert присъда["activated"] is False
    assert спирачка in присъда["blocked_by"]


def test_povtoreniyata_ne_sa_vtoro_dokazatelstvo(monkeypatch):
    """Двайсет повторения с един независим свидетел не дават право.

    Пряко изискване: „Групово потвърждение на 57 позиции не е 57 независими
    свидетелства."
    """

    monkeypatch.delenv("LION_REBAR_RULE_ACTIVATION", raising=False)
    присъда = активиране.присъда(_кандидат(
        repeat_count=20, independent_observation_count=1,
        independent_drawing_family_count=1, project_count=1))
    assert присъда["activated"] is False
    assert "ONE_WITNESS" in присъда["blocked_by"]


def test_edno_semeystvo_dava_semeyna_konvenciya_ne_obshto_pravilo(monkeypatch):
    """Два проекта в едно семейство дават адаптация, не пренос между семейства."""

    monkeypatch.delenv("LION_REBAR_RULE_ACTIVATION", raising=False)
    присъда = активиране.присъда(_кандидат(independent_drawing_family_count=1))
    assert присъда["scope"] == активиране.СЕМЕЙНА
    assert присъда["activated"] is True, присъда
    # А един проект и един свидетел стигат само до проектна конвенция.
    тесен = активиране.присъда(_кандидат(
        independent_observation_count=1, independent_drawing_family_count=1,
        project_count=1))
    assert тесен["scope"] == активиране.ПРОЕКТНА
    assert тесен["activated"] is False


def test_izklyucheniyat_rezhim_e_validen_i_se_zapisva(monkeypatch):
    """`LION_REBAR_RULE_ACTIVATION=off` дава нула и КАЗВА, че е контролен режим.

    Изискване: „Запази изключения режим като валидна контролна конфигурация."
    Затова тук не просто няма активирани — режимът е записан в изхода, за да може
    опитът да сравни трите състояния.
    """

    monkeypatch.setenv("LION_REBAR_RULE_ACTIVATION", "off")
    присъда = активиране.присъда(_кандидат())
    assert присъда["activated"] is False
    assert присъда["mode"] == "OFF_CONTROL_CONFIGURATION"
    assert "ACTIVATION_DISABLED" in присъда["blocked_by"]
    обобщено = активиране.обобщи([_кандидат()])
    assert обобщено["activated_rule_count"] == 0
    assert обобщено["mode"] == "OFF_CONTROL_CONFIGURATION"


def test_paketat_nosi_izchisleno_chislo_i_prichinite(tmp_path: Path, monkeypatch):
    """Пакетът записва изчисленото число И защо кандидатът не е активиран.

    Делтата срещу старото поведение: тогава `activated_rule_count` беше 0 без
    обяснение. Сега нулата идва с `blocked_by_counts`, тоест проверимо.
    """

    monkeypatch.delenv("LION_REBAR_RULE_ACTIVATION", raising=False)
    събитие = {
        "at": "2026-09-30T10:00:00+03:00",
        "event_type": "ENGINEER_RESOLVED",
        "project_name": "P1",
        "project_root": "C:/projects/P1",
        "annotation_handle": "A1",
        "annotation_text": "1N10x47",
        "payload": {
            "human_decision": {
                "schema": "lion-rebar-human-decision/v2",
                "event_id": "e1",
                "event_type": "ENGINEER_RESOLVED",
                "active": True,
                "annotation_handles": ["A1"],
                "source_identity": {"project_name": "P1", "source_sha256": "a" * 64},
                "payload": {
                    "annotation_handle": "A1",
                    "annotation_text": "1N10x47",
                    "action_class": "CORRECTION",
                    "reason_code": "WRONG_SHAPE",
                    "note": "Проверено",
                    "original": {"shape_code": "F001"},
                    "resolved": {"shape_code": "F008",
                                 "segment_lengths_cm": [10, 13, 21]},
                },
            }
        },
    }
    резултат = build_learning_pack(
        session_id="S-ACT", started_at=събитие["at"], ended_at=събитие["at"],
        events=[събитие], projects=[], output_dir=tmp_path)
    with zipfile.ZipFile(резултат.path) as архив:
        обобщение = json.loads(архив.read("session/summary.json"))
        кандидати = json.loads(архив.read("session/rule_candidates.json"))

    assert обобщение["candidate_count"] >= 1
    # Нулата остава — но вече казва ЗАЩО.
    assert обобщение["activated_rule_count"] == 0
    режим = обобщение["rule_activation"]
    assert режим["mode"] == "ON"
    assert режим["blocked_by_counts"], "нулата трябва да носи причина"
    присъда = кандидати["candidates"][0]["activation"]
    assert присъда["activated"] is False
    assert set(присъда["blocked_by"]) & {"ONE_WITNESS", "NO_AUDIT"}
