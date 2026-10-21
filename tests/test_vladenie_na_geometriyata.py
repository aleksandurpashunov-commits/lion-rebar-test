# -*- coding: utf-8 -*-
"""Операторски път за СПОРНО ВЛАДЕНИЕ на геометрия (§ 6.2).

Собственик, 02.10.2026: „§6 още не е затворен: липсващ операторски път за 52
позиции над 23 надписа. Той трябва да стане използваем през нормалния
интерфейс."

ИЗМЕРЕНО СЪСТОЯНИЕ ПРЕДИ. Замразената база от 29.09.2026: **52 позиции в 21
клъстера** (`d-2` 34, `Тест-002` 8, `s-9` 4, `s-7` 2, `s-8` 2, `тест-006` 2)
стояха спрени само защото нищо в програмата не позволяваше на оператора да каже
чия е спорната геометрия. Анализът маркираше конфликта правилно — еднаквият
надпис наистина не доказва еднакво решение — но изход нямаше.

КАКВО ПАЗИ ТОЗИ ФАЙЛ

* всеки член на спора се изброява поименно в решението; решение, което не
  покрива точно състава, се отказва (§ 6.2: групово решение с проверка на
  всеки член);
* решение за геометрия, която НЕ е в спор, се отказва — пътят решава спор, а
  не заобикаля проверка;
* при `OWNER` само собственикът изнася геометрията; останалите остават спрени
  с ясна причина, тоест са ОТДЕЛЕНИ, а не решени;
* при `SHARED` всички членове я изнасят, всеки със своето количество;
* спорът е по свързани компоненти: А–Б и Б–В са ЕДИН спор;
* решение от друг анализ не се наследява;
* оттеглянето връща спора.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lion_rebar.dxf.batch_geometry_report import _apply_global_geometry_conflicts
from lion_rebar.export.ownership_decisions import (
    ВИД_СПОДЕЛЕНА,
    ВИД_СОБСТВЕНИК,
    OwnershipDecisionError,
    audit_trail,
    geometry_key,
    load_ownership_decisions,
    record_ownership_decision,
    withdraw_ownership_decision,
)
from lion_rebar.project.data_identity import DataIdentity


def _самоличност(run: str = "R1") -> DataIdentity:
    return DataIdentity(
        project_id="P", source_sha256="S", source_generation_id="G", analysis_run_id=run
    )


def _ред(handle: str, текст: str, геометрия) -> dict:
    return {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": handle,
        "annotation_text": текст,
        "batch_status": "СИЛЕН",
        "candidate_handles": list(геометрия),
    }


def _спор(*, членове=("A", "B"), геометрия=("G1",)) -> list[dict]:
    return [_ред(h, "2N14x600/10", геометрия) for h in членове]


# --- анализът описва спора поименно ----------------------------------------


def test_sporat_nosi_klyucha_i_chlenovete_si():
    редове = _спор()

    _apply_global_geometry_conflicts(редове)

    for ред in редове:
        assert ред["global_conflict"] is True
        assert ред["ownership_conflict_key"] == ["G1"]
        assert ред["ownership_conflict_members"] == ["A", "B"]


def test_sporat_e_po_svarzani_komponenti():
    """А спори с Б, Б спори с В — значи и трите са в ЕДИН спор."""

    редове = [
        _ред("A", "2N14x600/10", ["G1"]),
        _ред("B", "2N14x600/10", ["G1", "G2"]),
        _ред("C", "2N14x600/10", ["G2"]),
    ]

    _apply_global_geometry_conflicts(редове)

    for ред in редове:
        assert ред["ownership_conflict_members"] == ["A", "B", "C"]
        assert ред["ownership_conflict_key"] == ["G1", "G2"]


# --- записът отказва всичко, което не е добросъвестно решение ---------------


def _членове(редове) -> dict:
    return {
        tuple(ред["ownership_conflict_key"]): tuple(ред["ownership_conflict_members"])
        for ред in редове
    }


def test_reshenie_za_negeometriya_v_spor_se_otkazva(tmp_path):
    with pytest.raises(OwnershipDecisionError):
        record_ownership_decision(
            tmp_path,
            kind=ВИД_СОБСТВЕНИК,
            key=["НЯМА_ТАКАВА"],
            owner_annotation_handle="A",
            member_handles=["A", "B"],
            decided_by="Александър",
            note="произволно",
            identity=_самоличност(),
            conflict_members={("G1",): ("A", "B")},
        )


def test_nepalen_sastav_se_otkazva(tmp_path):
    """Решение за част от спора не е решение на спора."""

    with pytest.raises(OwnershipDecisionError) as грешка:
        record_ownership_decision(
            tmp_path,
            kind=ВИД_СОБСТВЕНИК,
            key=["G1"],
            owner_annotation_handle="A",
            member_handles=["A"],
            decided_by="Александър",
            note="само за единия",
            identity=_самоличност(),
            conflict_members={("G1",): ("A", "B")},
        )
    assert "Непокрити" in str(грешка.value)


def test_sobstvenik_izvan_chlenovete_se_otkazva(tmp_path):
    with pytest.raises(OwnershipDecisionError):
        record_ownership_decision(
            tmp_path,
            kind=ВИД_СОБСТВЕНИК,
            key=["G1"],
            owner_annotation_handle="ЧУЖД",
            member_handles=["A", "B"],
            decided_by="Александър",
            note="чужд собственик",
            identity=_самоличност(),
            conflict_members={("G1",): ("A", "B")},
        )


@pytest.mark.parametrize("автор,мотив", [("", "мотив"), ("Александър", "")])
def test_bez_avtor_ili_motiv_se_otkazva(tmp_path, автор, мотив):
    with pytest.raises(OwnershipDecisionError):
        record_ownership_decision(
            tmp_path,
            kind=ВИД_СОБСТВЕНИК,
            key=["G1"],
            owner_annotation_handle="A",
            member_handles=["A", "B"],
            decided_by=автор,
            note=мотив,
            identity=_самоличност(),
            conflict_members={("G1",): ("A", "B")},
        )


# --- решението стига до анализа --------------------------------------------


def _запиши(tmp_path, *, вид, собственик="", членове=("A", "B"), геометрия=("G1",),
            run="R1"):
    return record_ownership_decision(
        tmp_path,
        kind=вид,
        key=list(геометрия),
        owner_annotation_handle=собственик,
        member_handles=list(членове),
        decided_by="Александър",
        note="геометрията е начертана веднъж за двата надписа",
        identity=_самоличност(run),
        conflict_members={geometry_key(геометрия): tuple(членове)},
    )


def test_sobstvenikat_ya_iznasya_a_drugiyat_ostava_spryan(tmp_path):
    _запиши(tmp_path, вид=ВИД_СОБСТВЕНИК, собственик="A")
    решения = load_ownership_decisions(tmp_path, _самоличност())
    редове = _спор()

    _apply_global_geometry_conflicts(редове, решения)

    по_handle = {ред["annotation_handle"]: ред for ред in редове}
    # Собственикът: спорът е решен и не го спира.
    assert по_handle["A"]["global_conflict"] is False
    assert по_handle["A"]["ownership_assigned_elsewhere"] is False
    assert по_handle["A"]["ownership_decision_owner"] == "A"
    # Другият: ОТДЕЛЕН, не решен — остава за ръчен преглед с ясна причина.
    assert по_handle["B"]["global_conflict"] is False
    assert по_handle["B"]["ownership_assigned_elsewhere"] is True
    assert по_handle["B"]["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert any(
        "присъдена на надпис A" in бележка
        for бележка in по_handle["B"]["candidate_notes"]
    )


def test_spodelenata_geometriya_osvobozhdava_vsichki(tmp_path):
    _запиши(tmp_path, вид=ВИД_СПОДЕЛЕНА)
    решения = load_ownership_decisions(tmp_path, _самоличност())
    редове = _спор()

    _apply_global_geometry_conflicts(редове, решения)

    for ред in редове:
        assert ред["global_conflict"] is False
        assert ред["ownership_assigned_elsewhere"] is False
        assert ред["ownership_decision_kind"] == ВИД_СПОДЕЛЕНА


def test_reshenie_varhu_drug_sastav_ne_reshava_tozi_spor(tmp_path):
    """Трети надпис влиза в спора: старото решение вече не го покрива."""

    _запиши(tmp_path, вид=ВИД_СОБСТВЕНИК, собственик="A", членове=("A", "B"))
    решения = load_ownership_decisions(tmp_path, _самоличност())
    редове = _спор(членове=("A", "B", "C"))

    _apply_global_geometry_conflicts(редове, решения)

    for ред in редове:
        assert ред["global_conflict"] is True, ред["annotation_handle"]


def test_reshenie_ot_drug_analiz_ne_se_nasledyava(tmp_path):
    _запиши(tmp_path, вид=ВИД_СОБСТВЕНИК, собственик="A", run="R1")

    assert load_ownership_decisions(tmp_path, _самоличност("R2")) == {}


def test_ottegliane_varshta_spora(tmp_path):
    _запиши(tmp_path, вид=ВИД_СОБСТВЕНИК, собственик="A")

    assert withdraw_ownership_decision(
        tmp_path, key=["G1"], withdrawn_by="Александър", note="сгрешен собственик"
    )
    assert load_ownership_decisions(tmp_path, _самоличност()) == {}

    редове = _спор()
    _apply_global_geometry_conflicts(редове, {})
    assert all(ред["global_conflict"] for ред in редове)


def test_ottegliane_na_nesashtestvuvashto_e_False(tmp_path):
    assert not withdraw_ownership_decision(
        tmp_path, key=["G9"], withdrawn_by="Александър", note="няма такова"
    )


def test_odiotnata_sleda_pazi_i_dvete_deystviya(tmp_path):
    _запиши(tmp_path, вид=ВИД_СОБСТВЕНИК, собственик="A")
    withdraw_ownership_decision(
        tmp_path, key=["G1"], withdrawn_by="Александър", note="сгрешен собственик"
    )

    следа = audit_trail(tmp_path)

    assert [ред["event"] for ред in следа] == [
        "OWNERSHIP_RECORDED",
        "OWNERSHIP_WITHDRAWN",
    ]
    assert следа[0]["decided_by"] == "Александър"
    assert следа[1]["withdrawn_by"] == "Александър"


def test_nechetim_fayl_e_otkaz_a_ne_prazno(tmp_path):
    """§ 7.6: решенията на оператора не се подменят мълчаливо с празно."""

    работна = tmp_path / "working"
    работна.mkdir(parents=True)
    (работна / "geometry_ownership_decisions.json").write_text(
        "{непълен", encoding="utf-8"
    )

    with pytest.raises(OwnershipDecisionError):
        load_ownership_decisions(tmp_path, _самоличност())


# --- портата към експорта ---------------------------------------------------


def _прост_ред(handle: str, **промени) -> dict:
    ред = {
        "annotation_handle": handle,
        "annotation_text": "2N14x600",
        "annotation_class": "ACTIONABLE",
        "annotation_count": 2,
        "diameter_mm": 14.0,
        "declared_length_cm": 600.0,
        "batch_status": "СИЛЕН",
        "matcher_disposition": "CONTROL",
        "global_conflict": False,
        "data_quality_flags": [],
        "quantity_review_required": False,
        "master_shape_code": "F001",
        "candidate_handles": ["G1"],
        "candidate_notes": [],
        "canonical_segment_lengths_cm": [600.0],
        "production_segments_cm": [600.0],
    }
    ред.update(промени)
    return ред


def test_otdelenata_pozitsia_ne_vliza_v_eksporta(tmp_path):
    """Мери се през `build_batch_export_context` — единственият път към изхода."""

    from lion_rebar.engine.models import (
        EnginePackage,
        SegmentDefinition,
        ShapeDefinition,
    )
    from lion_rebar.export.batch_auto_output import build_batch_export_context

    отчет = tmp_path / "batch.json"
    отчет.write_text(
        json.dumps(
            {
                "cancelled": False,
                "rows": [
                    _прост_ред("A"),
                    _прост_ред("B", ownership_assigned_elsewhere=True),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    engine = EnginePackage(
        engine_id="test-engine",
        title="TEST",
        version="0.3.12",
        production_ready=True,
        shapes=[
            ShapeDefinition(
                code="F001", name="ПРАВ ПРЪТ", state="LOCKED", locked=True,
                segments=[SegmentDefinition(index=1, length_code="L1")],
            )
        ],
    )

    from lion_rebar.dxf.models import DxfEntityRecord

    обекти = [
        DxfEntityRecord("FRAME", "TEXT", "$Ramka", {"insert": [0, 0, 0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord("TITLE", "TEXT", "0", {"insert": [100, 0, 0]}, "АРМИРОВКА ГРЕДИ"),
        DxfEntityRecord("BEAM", "TEXT", "0", {"insert": [100, 750, 0]}, "Гр. B.1 25/50"),
        DxfEntityRecord("A", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [100, 500, 0]},
                        "2N14x600"),
        DxfEntityRecord("B", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [120, 500, 0]},
                        "2N14x600"),
        DxfEntityRecord("G1", "LINE", "Arm - N14",
                        {"start": [100, 500, 0], "end": [700, 500, 0]}),
    ]

    контекст = build_batch_export_context(отчет, entities=обекти, engine=engine)

    блокери = {б.annotation_handle: б for б in контекст.blockers}
    assert "OWNERSHIP_ASSIGNED_ELSEWHERE" in блокери["B"].reason_codes
    # А собственикът минава: решението не го спира.
    assert "A" not in блокери
