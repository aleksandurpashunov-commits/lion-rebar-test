# -*- coding: utf-8 -*-
"""Решението за владение преживява нов анализ на СЪЩАТА редакция на чертежа.

ДЕФЕКТЪТ. Операторът решава спора, програмата записва решението с
``analysis_run_id`` на анализа, върху който е взето, и казва „Пусни пакетния
анализ наново“. Новият анализ започва с ``begin_analysis_run`` — нов
``analysis_run_id`` — а ``load_ownership_decisions`` сверяваше точно него.
Решението умираше от собствената си инструкция. Измерено на ``d-2``: 17
прочетени преди нов анализ, 0 след. В корпуса от 22.10.2026 спорното владение
е единствената причина за 51 от 254-те спрени позиции.

РЕШЕНИЕ НА СОБСТВЕНИКА, 22.10.2026: „Решението за владение да преживява нов
анализ на същата редакция на чертежа, както начертаните рамки.“

ЗАЩО Е БЕЗОПАСНО. Причината за старото сверяване беше, че нов прогон може да
даде други спорове. Това се пази на мястото, където решението се ПРИЛАГА:
``_apply_global_geometry_conflicts`` приема решение само ако новият анализ дава
същата геометрия с ТОЧНО същите спорещи надписи. Самоличността на чертежа
(проект, генерация, sha256) се сверява както преди — друга редакция не
наследява нищо.
"""
from __future__ import annotations

from types import SimpleNamespace

from lion_rebar.dxf.batch_geometry_report import _apply_global_geometry_conflicts
from lion_rebar.export.ownership_decisions import (
    ВИД_СОБСТВЕНИК,
    geometry_key,
    load_ownership_decisions,
    record_ownership_decision,
)
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.ui.batch_geometry_worker import BatchGeometryWorker


def _самоличност(
    run: str = "R1", *, project: str = "P", sha: str = "S", generation: str = "G"
) -> DataIdentity:
    return DataIdentity(
        project_id=project,
        source_sha256=sha,
        source_generation_id=generation,
        analysis_run_id=run,
    )


def _спор(членове=("A", "B"), геометрия=("G1",)) -> list[dict]:
    return [
        {
            "annotation_class": "ACTIONABLE",
            "annotation_handle": handle,
            "annotation_text": "2N14x600/10",
            "batch_status": "СИЛЕН",
            "candidate_handles": list(геометрия),
        }
        for handle in членове
    ]


def _запиши_в_R1(корен) -> None:
    record_ownership_decision(
        корен,
        kind=ВИД_СОБСТВЕНИК,
        key=["G1"],
        owner_annotation_handle="A",
        member_handles=["A", "B"],
        decided_by="Александър",
        note="геометрията е начертана за надпис A",
        identity=_самоличност("R1"),
        conflict_members={geometry_key(["G1"]): ("A", "B")},
    )


def test_reshenieto_se_chete_sled_nov_analiz_na_sashtata_redakciya(tmp_path):
    _запиши_в_R1(tmp_path)

    решения = load_ownership_decisions(tmp_path, _самоличност("R2"))

    assert set(решения) == {("G1",)}
    assert решения[("G1",)].owns("A") and not решения[("G1",)].owns("B")


def test_novi_yat_analiz_prilaga_reshenieto(tmp_path):
    """Целият ход: запис в R1 → нов анализ R2 → спорът е решен."""

    _запиши_в_R1(tmp_path)
    редове = _спор()

    _apply_global_geometry_conflicts(
        редове, load_ownership_decisions(tmp_path, _самоличност("R2"))
    )

    по_handle = {ред["annotation_handle"]: ред for ред in редове}
    assert по_handle["A"]["global_conflict"] is False
    assert по_handle["A"]["ownership_assigned_elsewhere"] is False
    assert по_handle["B"]["ownership_assigned_elsewhere"] is True


def test_rabotnikat_na_analiza_chete_reshenieto_s_novata_samolichnost(tmp_path):
    """Точно пътят на прозореца: работникът получава самоличността на НОВИЯ
    прогон от ``begin_analysis_run`` и чете решенията с нея."""

    _запиши_в_R1(tmp_path)
    работник = SimpleNamespace(
        _project=SimpleNamespace(root=tmp_path),
        _data_identity=_самоличност("R2"),
    )

    решения = BatchGeometryWorker._load_ownership_decisions(работник)

    assert set(решения) == {("G1",)}


# --- това, което НЕ се наследява, остава ненаследено -----------------------


def test_druga_generaciya_na_chertezha_ne_nasledyava(tmp_path):
    _запиши_в_R1(tmp_path)

    assert load_ownership_decisions(tmp_path, _самоличност("R2", generation="G2")) == {}


def test_drugo_sadarzhanie_na_chertezha_ne_nasledyava(tmp_path):
    _запиши_в_R1(tmp_path)

    assert load_ownership_decisions(tmp_path, _самоличност("R2", sha="S2")) == {}


def test_drug_proekt_ne_nasledyava(tmp_path):
    _запиши_в_R1(tmp_path)

    assert load_ownership_decisions(tmp_path, _самоличност("R2", project="P2")) == {}


def test_noviyat_analiz_s_drug_sastav_na_spora_ne_e_reshen(tmp_path):
    """Нов прогон даде трети спорещ надпис: старото решение не покрива този
    спор и позициите остават спрени — това беше смисълът на старото
    сверяване по прогон, и то се пази тук."""

    _запиши_в_R1(tmp_path)
    редове = _спор(членове=("A", "B", "C"))

    _apply_global_geometry_conflicts(
        редове, load_ownership_decisions(tmp_path, _самоличност("R2"))
    )

    assert all(ред["global_conflict"] is True for ред in редове)
