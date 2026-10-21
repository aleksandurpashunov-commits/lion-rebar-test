# -*- coding: utf-8 -*-
"""Паметта участва в НОРМАЛНИЯ анализ, преди ръчната работа.

Собственик, 30.09.2026: „Паметта трябва да участва в нормалния анализ, преди
случаят да бъде изпратен за ръчна работа."

ДЕЛТАТА, която този файл пази. До 30.09 указателят с предложения
(`review/decision_suggestions.DecisionSuggestionIndex`) се питаше САМО когато
операторът отвори редактора — `main_window._decision_suggestion_for_row`. Тоест
паметта влизаше в игра, СЛЕД като ръчната работа вече е започнала: анализът
изпращаше случая за преглед, без да погледне дали същият случай вече е решен.

Сега проходът `_apply_decision_memory_suggestions` върви В анализа, след всички
доказатели. Това е НИВО Б по определението на собственика: намира релевантния
пример, казва какво съвпада и какво липсва, предлага — и **не прилага**.
Производствените полета не се пипат; приемането е изрично действие в редактора.

Полетата на реда и на решението са пълни нарочно: строгият контекст
(`strict_context_from_review_row`) иска именно тях и без тях предложение не се
дава. Това не е шум в теста, а условието за годност.
"""
from __future__ import annotations

import json
from pathlib import Path

from lion_rebar.dxf.batch_geometry_report import _apply_decision_memory_suggestions
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.review.decision_suggestions import DecisionSuggestionIndex
from lion_rebar.session.decision_contract import (
    append_project_decision_event,
    mark_project_decision_delivered,
)

ENGINE = "0.3.15"
SHA = "d" * 64


def _самоличност_речник(самоличност: DataIdentity) -> dict:
    return {
        "project_id": самоличност.project_id,
        "source_sha256": самоличност.source_sha256,
        "source_generation_id": самоличност.source_generation_id,
        "analysis_run_id": самоличност.analysis_run_id,
        "engine_version": ENGINE,
    }


def _проект(tmp_path: Path) -> tuple[Path, DataIdentity]:
    корен = tmp_path / "проект"
    (корен / "working").mkdir(parents=True)
    самоличност = DataIdentity(
        project_id="p-1", source_sha256=SHA,
        source_generation_id="g-1", analysis_run_id="a-1",
    )
    (корен / "working" / "project.json").write_text(json.dumps({
        "name": "ПАМЕТ",
        "engine_id": "lion-rebar-master",
        "engine_version": ENGINE,
        "engine_sha256": "e" * 64,
        "extensions": {"data_identity": _самоличност_речник(самоличност)},
    }, ensure_ascii=False), encoding="utf-8")
    return корен, самоличност


def _ред(handle: str) -> dict:
    """Ред за преглед — с полетата, които строгият контекст изисква."""

    return {
        "annotation_handle": handle,
        "annotation_text": "1N10x47",
        "annotation_class": "ACTIONABLE",
        "annotation_count": 1,
        "count": 1,
        "diameter_mm": 10,
        "declared_length_cm": 47,
        "master_shape_code": "F001",
        "candidate_handles": ["G-%s" % handle],
        "candidate_basis": "DXF_HANDLES",
        "batch_status": "REVIEW",
        "matcher_disposition": "MATCHER_REVIEW",
        "matcher_evidence_band": "MEDIUM",
        "candidate_confidence": "MEDIUM",
        "review_reason_codes": ["SHAPE_REVIEW"],
        "contextual_rule_code": "",
        "canonical_signature": "candidate-v1",
        "numeric_difference": 0,
        "alternative_candidate_count": 0,
        "frame_category_code": "DETAIL",
        "global_conflict": False,
        "geometry_owner_conflict": False,
        "candidate_notes": [],
    }


def _реши(корен: Path, самоличност: DataIdentity, handle: str,
          *, форма: str = "F008", участъци=(13, 21, 13)) -> dict:
    """Записва потвърдено човешко решение и го отбелязва като ДОСТАВЕНО.

    Годни за предложение са само доставените решения — в прозореца това става при
    нормално приключване на сесията. Затова тук минава и
    `mark_project_decision_delivered`: без него решението е записано, но още не е
    доказателство, и точно това разграничение собственикът иска да се пази.
    """

    преди = _ред(handle)
    товар = {
        "annotation_handle": handle,
        "annotation_text": преди["annotation_text"],
        "action_class": "CORRECTION",
        "reason_code": "WRONG_SHAPE",
        "reason": "Грешна MASTER форма",
        "note": "Проверено по детайла",
        "engine_version": ENGINE,
        "data_identity": _самоличност_речник(самоличност),
        "row_context": dict(преди),
        "annotation": {"handle": handle, "text": преди["annotation_text"],
                       "resolved_text": "1N10x47"},
        "original": {
            "count": 1, "diameter_mm": 10, "declared_length_cm": 47,
            "shape_code": "F001", "segment_lengths_cm": [47],
            "geometry_handles": ["G-%s" % handle],
            "candidate_basis": "DXF_HANDLES",
            "review_reason_codes": ["SHAPE_REVIEW"],
            "batch_status": "REVIEW",
            "matcher_disposition": "MATCHER_REVIEW",
        },
        "resolved": {
            "shape_code": форма, "shape_name": форма, "shape_revision": 1,
            "count": 1, "diameter_mm": 10, "production_length_cm": 47,
            "segment_lengths_cm": list(участъци), "turn_angles_deg": [90, 90],
        },
        "geometry": {"handles": ["G-%s" % handle], "layer": "ARM-N10",
                     "basis": "DXF_HANDLES"},
    }
    плик = append_project_decision_event(корен, "ENGINEER_RESOLVED", товар)
    mark_project_decision_delivered(корен, плик["event_id"], session_id="s-1")
    return плик


def _указател(корен: Path) -> DecisionSuggestionIndex:
    return DecisionSuggestionIndex.from_sources(
        session_db_path=None, project_root=корен)


def _питай(ред: dict, указател, самоличност) -> dict:
    """Пита паметта ТОЧНО както я пита производственият път.

    Дотук тук стоеше ``{"version": ENGINE}``. Такъв ключ производството
    НИКОГА не праща: и ``ui/main_window``, и безинтерфейсният
    ``full_path.py`` подават ``engine_version``. Заради това шестте теста
    бяха зелени, докато механизмът отказваше ВСЕКИ ред в истинска работа
    (296 от 296 в `s-8`). Намерено от приемателния опит по § 8 на
    02.10.2026; оттук нататък тестът ползва производствения ключ.
    """

    return _apply_decision_memory_suggestions(
        [ред], memory=указател, data_identity=самоличност,
        engine_metadata=_производствена_метадата())


def _производствена_метадата() -> dict:
    """Същите ключове, които подават двата производствени пътя."""

    return {
        "engine_id": "lion-rebar-master",
        "engine_version": ENGINE,
        "content_sha256": "e" * 64,
    }


# ==========================================================================


def test_analizat_pita_pametta_predi_rachnata_rabota(tmp_path: Path):
    """Решен случай дава предложение на СЛЕДВАЩИЯ подобен, още в анализа.

    Това е делтата: до 30.09 този ред щеше да излезе за ръчна работа, без нищо
    да е погледнало вече взетото решение.
    """

    корен, самоличност = _проект(tmp_path)
    _реши(корен, самоличност, "AAA1")
    указател = _указател(корен)
    assert указател.source_stats.eligible_evidence_count == 1, (
        "решението не е станало годно доказателство: %s" % (указател.source_stats,))

    нов = _ред("BBB2")
    сметка = _питай(нов, указател, самоличност)

    assert сметка.get("READY") == 1, сметка
    памет = нов["decision_memory"]
    assert памет["status"] == "READY"
    assert памет["can_accept"] is True
    assert памет["proposal"], "предложението трябва да носи стойности за показване"


def test_predlozhenieto_ne_prilaga_i_se_vizhda_v_belezhkite(tmp_path: Path):
    """НИВО Б: предложение с основание, но БЕЗ прилагане."""

    корен, самоличност = _проект(tmp_path)
    _реши(корен, самоличност, "AAA1")
    нов = _ред("BBB2")
    _питай(нов, _указател(корен), самоличност)

    # Производствените полета остават непипани.
    assert "production_segments_cm" not in нов
    assert "canonical_segment_lengths_cm" not in нов
    assert нов["master_shape_code"] == "F001"
    assert нов["batch_status"] == "REVIEW"
    # А операторът вижда откъде идва и че не е приложено.
    бележки = " ".join(нов["candidate_notes"])
    assert "ПРЕДЛОЖЕНО ОТ ПРЕДИШЕН СЛУЧАЙ" in бележки
    assert "не е приложено" in бележки


def test_sobstveniyat_handle_ne_si_predlaga_svoeto_reshenie(tmp_path: Path):
    """Решеният handle не получава предложение от собственото си решение."""

    корен, самоличност = _проект(tmp_path)
    _реши(корен, самоличност, "AAA1")
    същият = _ред("AAA1")
    сметка = _питай(същият, _указател(корен), самоличност)
    assert сметка.get("READY") is None, сметка


def test_nedostaveno_reshenie_ne_e_dokazatelstvo(tmp_path: Path):
    """Записано, но недоставено решение НЕ дава предложение.

    Разграничението „записано" срещу „доставено" е изискване на собственика:
    „Системата не трябва да съобщава „запомнено", преди записът да е траен", и
    повторен анализ не превръща същото действие в ново доказателство.
    """

    корен, самоличност = _проект(tmp_path)
    преди = _ред("AAA1")
    товар = {
        "annotation_handle": "AAA1",
        "annotation_text": преди["annotation_text"],
        "action_class": "CORRECTION",
        "reason_code": "WRONG_SHAPE",
        "note": "Проверено по детайла",
        "engine_version": ENGINE,
        "data_identity": _самоличност_речник(самоличност),
        "row_context": dict(преди),
        "annotation": {"handle": "AAA1", "text": преди["annotation_text"]},
        "original": {"count": 1, "diameter_mm": 10, "declared_length_cm": 47,
                     "shape_code": "F001", "segment_lengths_cm": [47],
                     "geometry_handles": ["G-AAA1"],
                     "candidate_basis": "DXF_HANDLES"},
        "resolved": {"shape_code": "F008", "shape_name": "F008",
                     "shape_revision": 1, "count": 1, "diameter_mm": 10,
                     "production_length_cm": 47,
                     "segment_lengths_cm": [13, 21, 13],
                     "turn_angles_deg": [90, 90]},
        "geometry": {"handles": ["G-AAA1"], "layer": "ARM-N10",
                     "basis": "DXF_HANDLES"},
    }
    # ЗАПИСВА се, но НЕ се отбелязва като доставено.
    append_project_decision_event(корен, "ENGINEER_RESOLVED", товар)

    нов = _ред("BBB2")
    сметка = _питай(нов, _указател(корен), самоличност)
    assert сметка.get("READY") is None, сметка


def test_bez_pamet_analizat_e_kakto_predi(tmp_path: Path):
    """Без указател проходът не прави нищо — изключената памет е валиден режим."""

    ред = _ред("BBB2")
    сметка = _apply_decision_memory_suggestions(
        [ред], memory=None, data_identity=None, engine_metadata=None)
    assert сметка == {}
    assert "decision_memory" not in ред
    assert ред["candidate_notes"] == []


def test_nechetimo_hranilishte_ne_spira_analiza():
    """Повредена памет оставя реда без предложение, но анализът минава.

    Изискване: „Повредено или изрично посочено липсващо хранилище не се подменя
    мълчаливо с празно." Тук не се подменя — грешката се брои и се вижда.
    """

    class _Развален:
        def lookup(self, row, *, identity, engine_version):
            raise RuntimeError("повредено хранилище")

    ред = _ред("BBB2")
    сметка = _apply_decision_memory_suggestions(
        [ред], memory=_Развален(),
        data_identity=DataIdentity("p", SHA, "g"),
        engine_metadata={"version": ENGINE})
    assert сметка == {"ГРЕШКА": 1}
    assert "decision_memory" not in ред


def test_proizvodstveniyat_klyuch_za_versiyata_stiga_do_ukazatelya():
    """КЛЮЧЪТ Е ``engine_version``, не ``version`` (намерено от опита по § 8).

    Дотук анализът четеше ``engine_metadata["version"]``, а и двата
    производствени пътя — ``ui/main_window`` и безинтерфейсният ``full_path.py``
    — подават ``engine_version``. Версията беше празна при всяко истинско
    извикване и указателят отказваше ВСЕКИ ред: измерено 296 от 296 реда в
    ``s-8`` и 179 от 179 в ``d-2``.

    Тестът пита с ТОЧНО производствения речник и проверява какво е стигнало до
    указателя. Старото четене го проваля.
    """

    видяно: list[str] = []

    class _Записващ:
        def lookup(self, row, *, identity, engine_version):
            видяно.append(str(engine_version))
            raise RuntimeError("не ни трябва отговор, а подаденото")

    _apply_decision_memory_suggestions(
        [_ред("VER1")],
        memory=_Записващ(),
        data_identity=DataIdentity("p", SHA, "g"),
        engine_metadata=_производствена_метадата(),
    )

    assert видяно == [ENGINE], "версията не стига до указателя"


def test_stariyat_klyuch_version_prodalzhava_da_raboti():
    """По-стар викащ не бива да спре да работи заради поправката."""

    видяно: list[str] = []

    class _Записващ:
        def lookup(self, row, *, identity, engine_version):
            видяно.append(str(engine_version))
            raise RuntimeError("не ни трябва отговор")

    _apply_decision_memory_suggestions(
        [_ред("VER2")],
        memory=_Записващ(),
        data_identity=DataIdentity("p", SHA, "g"),
        engine_metadata={"version": ENGINE},
    )

    assert видяно == [ENGINE]
