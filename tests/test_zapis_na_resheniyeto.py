# -*- coding: utf-8 -*-
"""Записът на решението: петте полета и ВИДЪТ на решението (§ 3.2 и § 3.3).

Задание от 30.09.2026. Пликът вече носеше самоличност, стабилен ``event_id``,
``decision_revision``, ``supersedes_event_ids``, ``active`` и ``operation_kind``.
Липсваха точно пет полета: предишната и новата стойност, авторът, относимите
доказателства, обхватът и версията на кода — плюс разграничението между
наблюдение, предложение, потвърдено решение, отхвърляне и поправка.

ДЕЛТАТА, която този файл пази:

* ``change`` различава ПОТВЪРЖДЕНИЕ от ПОПРАВКА по данните, не по типа на
  събитието. Операторът, приел предложението на програмата без промяна, не е
  коригирал нищо;
* предишната стойност НЕ се измисля. Липсва ли — записва се като липсваща, с
  причина, и НЕ се подменя с новата;
* ``before_keys`` показва с кой ключ е прочетена всяка стойност. Дължината от
  надписа (``declared_length_cm``) и производствената дължина не се сливат зад
  общо име;
* ``actor`` не присвоява име. ``"USER"`` се записва както е, но не посочва
  човек, а липсващият автор остава липсващ — акаунтът на машината не става
  автор на инженерно решение;
* обхватът е най-тесният доказан: едно операторско действие е решение за
  позиция и не става правило;
* старите пликове (схема v2) остават четими — записано решение не губи силата
  си, защото кодът е научил нови полета.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from lion_rebar.config.constants import APP_VERSION
from lion_rebar.learning.scopes import ОБХВАТ_ПОЗИЦИЯ, ОБХВАТ_ПРОЕКТ
from lion_rebar.session.decision_contract import (
    DECISION_CONTRACT_SCHEMA,
    DECISION_CONTRACT_SCHEMA_V2,
    JOURNAL_NAME,
    append_project_decision_event,
    is_supported_decision_contract,
)
from lion_rebar.session.decision_fields import (
    ВИД_НАБЛЮДЕНИЕ,
    ВИД_НЕУСТАНОВЕНО,
    ВИД_ОТХВЪРЛЯНЕ,
    ВИД_ПОПРАВКА,
    ВИД_ПОТВЪРДЕНО,
    ВИД_ПРЕДЛОЖЕНИЕ,
)

ENGINE = "0.3.15"
SHA = "c" * 64


def _проект(tmp_path: Path) -> Path:
    корен = tmp_path / "проект"
    (корен / "working").mkdir(parents=True)
    (корен / "working" / "project.json").write_text(
        json.dumps(
            {
                "project_id": "p-1",
                "name": "ЗАПИС",
                "engine_id": "lion-rebar-master",
                "engine_version": ENGINE,
                "engine_sha256": "e" * 64,
                "extensions": {"source_sha256": SHA},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return корен


def _решение(**промени) -> dict:
    """Товар на инженерно решение, както го подава редакторът."""

    товар = {
        "annotation_handle": "A1",
        "annotation_text": "12 N12 L=240",
        "reason_code": "GEOMETRY_CONFLICT",
        "reason": "Надписът и геометрията не съвпадат.",
        "original": {
            "count": 12,
            "diameter_mm": 12,
            "declared_length_cm": 240,
            "shape_code": "F001",
            "segment_lengths_cm": [240],
        },
        "resolved": {
            "count": 12,
            "diameter_mm": 12,
            "production_length_cm": 240,
            "shape_code": "F001",
            "segment_lengths_cm": [240],
        },
        "evidence": {"source": "DXF", "note": "два отпечатани размера"},
    }
    товар.update(промени)
    return товар


# --- § 3.3 петте полета -----------------------------------------------------


def test_promyanata_nosi_predishna_i_nova_stoynost(tmp_path):
    корен = _проект(tmp_path)
    товар = _решение()
    товар["resolved"] = dict(товар["resolved"], production_length_cm=255, count=10)

    плик = append_project_decision_event(корен, "ENGINEER_RESOLVED", товар)

    промяна = плик["change"]
    assert промяна["before"]["production_length_cm"] == 240.0
    assert промяна["after"]["production_length_cm"] == 255.0
    assert промяна["before"]["count"] == 12.0
    assert промяна["after"]["count"] == 10.0
    assert промяна["changed_fields"] == ["count", "production_length_cm"]
    assert промяна["unchanged_fields"] == [
        "diameter_mm",
        "segment_lengths_cm",
        "shape_code",
    ]
    assert промяна["before_source"] == "PAYLOAD_ORIGINAL"
    assert промяна["after_source"] == "PAYLOAD_RESOLVED"


def test_izvorat_na_vsyaka_stoynost_se_zapisva(tmp_path):
    """Дължината от надписа не се слива с производствената зад общо име."""

    корен = _проект(tmp_path)
    плик = append_project_decision_event(корен, "ENGINEER_RESOLVED", _решение())

    промяна = плик["change"]
    assert промяна["before_keys"]["production_length_cm"] == "declared_length_cm"
    assert промяна["after_keys"]["production_length_cm"] == "production_length_cm"


def test_lipsvashtata_predishna_stoynost_ne_se_izmislya(tmp_path):
    корен = _проект(tmp_path)
    товар = {
        "annotation_handle": "B2",
        "annotation_text": "8 N10",
        "geometry_handles": ["G1", "G2"],
        "master_shape_code": "F006",
        "declared_length_cm": 180,
    }

    плик = append_project_decision_event(корен, "APPROVED", товар)

    промяна = плик["change"]
    assert промяна["before"] is None
    assert промяна["before_source"] == "NO_PRIOR_RECORDED_VALUE"
    assert промяна["comparable"] is False
    # Новото се записва, без да се обявява за промяна спрямо несъществуващо старо.
    assert промяна["after"]["shape_code"] == "F006"
    assert промяна["changed_fields"] == []
    assert "shape_code" in промяна["fields_without_prior_value"]


def test_predishnata_stoynost_se_nasledyava_ot_otmeneno_reshenie(tmp_path):
    корен = _проект(tmp_path)
    append_project_decision_event(
        корен,
        "APPROVED",
        {
            "annotation_handle": "C3",
            "master_shape_code": "F001",
            "declared_length_cm": 200,
        },
    )

    втори = append_project_decision_event(
        корен,
        "APPROVED",
        {
            "annotation_handle": "C3",
            "master_shape_code": "F004",
            "declared_length_cm": 215,
        },
    )

    промяна = втори["change"]
    assert промяна["before_source"].startswith("SUPERSEDED_EVENT:")
    assert промяна["before"]["shape_code"] == "F001"
    assert промяна["after"]["shape_code"] == "F004"
    assert промяна["changed_fields"] == ["production_length_cm", "shape_code"]
    assert втори["decision_kind"]["kind"] == ВИД_ПОПРАВКА


def test_avtorat_ne_se_izmislya(tmp_path):
    корен = _проект(tmp_path)

    без = append_project_decision_event(корен, "ENGINEER_RESOLVED", _решение())
    assert без["actor"]["recorded_as"] is None
    assert без["actor"]["source"] == "UNRECORDED"
    assert без["actor"]["identifies_person"] is False
    assert без["actor"]["role"] is None

    безлично = append_project_decision_event(
        корен,
        "APPROVED",
        {"annotation_handle": "D4", "approved_by": "USER", "master_shape_code": "F001"},
    )
    assert безлично["actor"]["recorded_as"] == "USER"
    assert безлично["actor"]["source"] == "PAYLOAD:approved_by"
    # „USER" е роля, не човек. Записва се, но не брои за посочен автор.
    assert безлично["actor"]["identifies_person"] is False

    # Измерено на 01.10.2026: единайсет места пишат `operator_name or
    # "оператор"`, а `operator_name` не се задава никъде. Запълващият низ не
    # става автор — иначе полето връща в записа точно измислицата, която
    # трябва да изключи.
    запълващ = append_project_decision_event(
        корен,
        "APPROVED",
        {"annotation_handle": "D4b", "approved_by": "оператор", "master_shape_code": "F001"},
    )
    assert запълващ["actor"]["recorded_as"] == "оператор"
    assert запълващ["actor"]["identifies_person"] is False

    посочен = append_project_decision_event(
        корен,
        "APPROVED",
        {
            "annotation_handle": "E5",
            "approved_by": "инж. Пашунов",
            "master_shape_code": "F001",
            "session_id": "s-77",
        },
    )
    assert посочен["actor"]["identifies_person"] is True
    assert посочен["actor"]["recorded_as"] == "инж. Пашунов"
    assert посочен["actor"]["session_id"] == "s-77"


def test_dokazatelstvata_sa_na_edno_myasto(tmp_path):
    корен = _проект(tmp_path)
    товар = _решение(
        diagnostic_problem={"reason_code": "DIMENSION_CONFLICT"},
        review_reason_codes=["OWNERSHIP_CONFLICT"],
        matcher_evidence_band="STRONG",
    )

    плик = append_project_decision_event(корен, "ENGINEER_RESOLVED", товар)

    видове = {елемент["kind"] for елемент in плик["evidence"]["items"]}
    assert {"evidence", "diagnostic_problem", "review_reason_codes",
            "matcher_evidence_band", "reason_code", "reason"} <= видове
    assert плик["evidence"]["present"] is True
    assert плик["evidence"]["count"] == len(плик["evidence"]["items"])


def test_bez_dokazatelstva_poleto_e_yavno_prazno(tmp_path):
    корен = _проект(tmp_path)
    плик = append_project_decision_event(
        корен, "APPROVED", {"annotation_handle": "F6", "master_shape_code": "F001"}
    )
    assert плик["evidence"]["items"] == []
    assert плик["evidence"]["present"] is False


def test_obhvatat_e_nay_tesniyat_dokazan(tmp_path):
    корен = _проект(tmp_path)

    по_позиция = append_project_decision_event(корен, "ENGINEER_RESOLVED", _решение())
    assert по_позиция["decision_scope"]["scope"] == ОБХВАТ_ПОЗИЦИЯ
    assert по_позиция["decision_scope"]["positions"] == 1
    assert по_позиция["decision_scope"]["learned"] is False

    # Решение без handle-и (например отговор за категорията на една площ) важи
    # най-много за ТОЗИ проект — един независим източник.
    без_позиция = append_project_decision_event(
        корен, "ENGINEER_RESOLVED", {"area_id": "L+27.90", "reason_code": "AREA_CATEGORY"}
    )
    assert без_позиция["decision_scope"]["scope"] == ОБХВАТ_ПРОЕКТ
    assert без_позиция["decision_scope"]["independent_sources"] == 1


def test_versiyata_na_koda_e_v_zapisa(tmp_path):
    корен = _проект(tmp_path)
    плик = append_project_decision_event(корен, "ENGINEER_RESOLVED", _решение())
    assert плик["code_version"] == APP_VERSION
    # Engine-ът вече беше там и остава.
    assert плик["source_identity"]["engine_version"] == ENGINE


# --- § 3.2 петте вида -------------------------------------------------------


def test_prieto_predlozhenie_bez_promyana_e_potvarzhdenie(tmp_path):
    """Операторът, приел предложението както е, не е коригирал нищо."""

    корен = _проект(tmp_path)
    плик = append_project_decision_event(корен, "ENGINEER_RESOLVED", _решение())
    вид = плик["decision_kind"]
    assert вид["kind"] == ВИД_ПОТВЪРДЕНО
    assert вид["source"] == "DERIVED"
    assert вид["changed_fields"] == []


def test_razlichna_stoynost_e_popravka(tmp_path):
    корен = _проект(tmp_path)
    товар = _решение()
    товар["resolved"] = dict(товар["resolved"], shape_code="F004")
    плик = append_project_decision_event(корен, "ENGINEER_RESOLVED", товар)
    assert плик["decision_kind"]["kind"] == ВИД_ПОПРАВКА
    assert плик["decision_kind"]["changed_fields"] == ["shape_code"]


def test_bez_sravnima_predishna_stoynost_vidat_e_NEUSTANOVEN(tmp_path):
    """Липсваща предишна стойност не означава потвърждение.

    Уговорка на собственика, 01.10.2026: без надеждна база за сравнение
    системата не може да заключи, че производствените полета не са променени.
    Такъв случай остава изрично неустановен — „потвърдено" там би било извод от
    липсваща информация.
    """

    корен = _проект(tmp_path)
    плик = append_project_decision_event(
        корен,
        "APPROVED",
        {"annotation_handle": "N13", "master_shape_code": "F001",
         "declared_length_cm": 210},
    )

    assert плик["change"]["comparable"] is False
    вид = плик["decision_kind"]
    assert вид["kind"] == ВИД_НЕУСТАНОВЕНО
    assert вид["undetermined_because"] == "NO_PRIOR_RECORDED_VALUE"
    assert "Не е доказано" in вид["why"]


def test_otmenyane_bez_sravnima_stoynost_sashto_e_NEUSTANOVENO(tmp_path):
    """Отменяне без сравними стойности не доказва и поправка.

    Знае се само, че предходно решение е заместено. Дали нещо се е сменило не
    се знае, затова нито „потвърдено", нито „поправка".
    """

    корен = _проект(tmp_path)
    # Първото решение няма производствени полета, затова не оставя стойност,
    # която второто да сравни.
    append_project_decision_event(
        корен, "MANUAL_ASSIGNED", {"annotation_handle": "O14", "note": "без стойности"}
    )
    втори = append_project_decision_event(
        корен, "MANUAL_ASSIGNED", {"annotation_handle": "O14", "note": "пак без"}
    )

    assert len(втори["supersedes_event_ids"]) == 1
    assert втори["decision_kind"]["kind"] == ВИД_НЕУСТАНОВЕНО


def test_povtornoto_odobrenie_sas_sashtite_stoynosti_e_potvarzhdenie(tmp_path):
    """Отменянето само по себе си не е поправка.

    Второто одобрение отменя първото — такъв е животът на журнала. Но ако
    нито една производствена стойност не се е сменила, нищо не е поправено.
    """

    корен = _проект(tmp_path)
    товар = {
        "annotation_handle": "L12",
        "master_shape_code": "F001",
        "declared_length_cm": 200,
    }
    append_project_decision_event(корен, "APPROVED", товар)
    втори = append_project_decision_event(корен, "APPROVED", dict(товар))

    assert втори["decision_revision"] == 2
    assert len(втори["supersedes_event_ids"]) == 1
    assert втори["change"]["changed_fields"] == []
    assert втори["decision_kind"]["kind"] == ВИД_ПОТВЪРДЕНО


def test_izklyuchvaneto_e_othvarlyane(tmp_path):
    корен = _проект(tmp_path)
    плик = append_project_decision_event(
        корен,
        "NON_PRODUCTION_POSITION",
        {"annotation_handle": "G7", "reason_code": "NON_PRODUCTION_POSITION"},
    )
    assert плик["decision_kind"]["kind"] == ВИД_ОТХВЪРЛЯНЕ


def test_konstatiraneto_na_programata_e_nablyudenie(tmp_path):
    корен = _проект(tmp_path)
    плик = append_project_decision_event(
        корен, "MISSING_MASTER", {"annotation_handle": "H8"}
    )
    assert плик["decision_kind"]["kind"] == ВИД_НАБЛЮДЕНИЕ


def test_chernovata_e_predlozhenie(tmp_path):
    корен = _проект(tmp_path)
    плик = append_project_decision_event(
        корен, "MANUAL_SHAPE_DRAFT", {"annotation_handle": "I9"}
    )
    assert плик["decision_kind"]["kind"] == ВИД_ПРЕДЛОЖЕНИЕ


def test_ottegleno_e_popravka_i_ne_e_aktivno(tmp_path):
    корен = _проект(tmp_path)
    append_project_decision_event(
        корен, "GROUP_APPLIED", {"annotation_handle": "J10", "operation_id": "op-1"}
    )
    плик = append_project_decision_event(
        корен, "GROUP_UNDONE", {"annotation_handle": "J10", "operation_id": "op-1"}
    )
    assert плик["decision_kind"]["kind"] == ВИД_ПОПРАВКА
    assert плик["active"] is False


def test_vidat_moze_da_bade_podaden_ot_iztochnika(tmp_path):
    """Пътят на паметта знае, че ПРЕДЛАГА, а не решава — и го казва."""

    корен = _проект(tmp_path)
    плик = append_project_decision_event(
        корен,
        "APPROVED",
        {"annotation_handle": "K11", "decision_kind": "SUGGESTION"},
    )
    assert плик["decision_kind"]["kind"] == ВИД_ПРЕДЛОЖЕНИЕ
    assert плик["decision_kind"]["source"] == "PAYLOAD"


def test_grupovoto_reshenie_zapisva_chlenovete_poimenno(tmp_path):
    """57 потвърдени без членовете не позволява проверка на нито един (§ 6.1)."""

    корен = _проект(tmp_path)
    редове = [
        {
            "annotation_handle": "M%d" % индекс,
            "master_shape_code": "F006",
            "declared_length_cm": 120 + индекс,
        }
        for индекс in range(5)
    ]
    плик = append_project_decision_event(
        корен,
        "BULK_APPROVED",
        {
            "mode": "GEOMETRY_SAMPLE",
            "annotation_handles": [ред["annotation_handle"] for ред in редове],
            "decisions": редове,
        },
    )

    assert плик["operation_kind"] == "BULK"
    assert плик["change"]["member_count"] == 5
    записани = {член["annotation_handle"] for член in плик["change"]["members"]}
    assert записани == {"M0", "M1", "M2", "M3", "M4"}
    assert плик["change"]["members"][0]["after"]["shape_code"] == "F006"


# --- съвместимост и неизменност --------------------------------------------


def test_nova_shema_a_starata_ostava_chetima():
    assert DECISION_CONTRACT_SCHEMA.endswith("/v3")
    assert is_supported_decision_contract(DECISION_CONTRACT_SCHEMA)
    assert is_supported_decision_contract(DECISION_CONTRACT_SCHEMA_V2)
    assert not is_supported_decision_contract("lion-rebar-human-decision/v1")


def test_novite_poleta_ne_vlizat_v_identifikatora(tmp_path):
    """``event_id`` зависи само от входовете на v2 — проверено независимо.

    Идентификаторът се сглобява наново ТУК, от самоличността и товара, без да
    се чете полето на програмата. Ако петте нови полета бяха влезли в ключа,
    двете числа нямаше да съвпаднат и всяка записана досега памет щеше да
    получи нов идентификатор, тоест втора доставка за едно решение.
    """

    корен = _проект(tmp_path)
    товар = _решение()
    плик = append_project_decision_event(корен, "ENGINEER_RESOLVED", товар)

    ключ = {
        "event_type": "ENGINEER_RESOLVED",
        "identity": плик["source_identity"],
        "operation_id": "",
        "handles": ["A1"],
        "decision_revision": 1,
        "payload": товар,
    }
    отпечатък = hashlib.sha256(
        json.dumps(ключ, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    очакван = str(uuid.uuid5(uuid.NAMESPACE_URL, "lion-rebar:%s" % отпечатък))
    assert плик["event_id"] == очакван


def test_povtorna_dostavka_ostava_edna_dostavka(tmp_path):
    """Повторението със същия ``stable_event_id`` не дописва втори ред (§ 3.5)."""

    корен = _проект(tmp_path)
    товар = _решение()
    първи = append_project_decision_event(корен, "ENGINEER_RESOLVED", товар)
    повторение = dict(товар, stable_event_id=първи["event_id"])
    втори = append_project_decision_event(корен, "ENGINEER_RESOLVED", повторение)

    assert първи["event_id"] == втори["event_id"]
    редове = (корен / "working" / JOURNAL_NAME).read_text(
        encoding="utf-8"
    ).strip().splitlines()
    assert len(редове) == 1
    записано = json.loads(редове[0])
    assert записано["schema"] == DECISION_CONTRACT_SCHEMA
    # Петте полета стигат до диска, не само до върнатия плик.
    for поле in ("change", "actor", "evidence", "decision_scope", "code_version",
                 "decision_kind"):
        assert поле in записано
