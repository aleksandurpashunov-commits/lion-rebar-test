# -*- coding: utf-8 -*-
"""Проектната форма минава ЦЕЛИЯ нормален път — като един тест, не на части.

Точка 4 от `PRODALZHENIE.md`: „проектната форма в НОРМАЛНИЯ път, не само в
показен скрипт". Частите бяха покрити поотделно — кандидатът
(`test_project_shape_candidate`), диалогът (`test_new_shapes_dialog`), портата по
дефиниция (`test_born_shape_gate`), предложенията в диалозите
(`test_approval_prefill`) — но нищо не минаваше веригата наведнъж. А най-скъпите
дефекти в този проект са били именно връзки между верни части.

Тук един тест минава: **избрана геометрия → кандидат на проекта → чернова в
Engine Studio → контролен тест → одобрение → публикуване с контролната фраза →
формата се намира от индекса, с който САМИЯТ анализ разпознава.**

Какво НЕ се мени: формата пак се ражда като чернова, пак минава контролни тестове
и одобрение, и пак се публикува с контролна фраза. Тестът не заобикаля нито една
от тези стъпки — ако някоя изчезне, той пада.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lion_rebar.engine.legacy_import import import_shape_draft_json
from lion_rebar.engine.project_shape_candidate import (
    build_project_shape_candidate,
    write_project_shape_candidate,
)
from lion_rebar.engine.repository import (
    APPROVAL_PHRASE,
    EngineRepository,
    EngineRepositoryError,
)
from lion_rebar.engine.shape_index import EngineShapeIndex
from lion_rebar.engine.test_runner import build_control_test, run_engine_tests

#: Трите върха на реална избрана геометрия: две рамена 100 и 50 cm, завой 90°
#: надясно. Числата са примерни — в кода няма нито handle, нито конкретен размер.
ТОЧКИ = ((10.0, 10.0), (110.0, 10.0), (110.0, -40.0))


def _товар() -> dict:
    return {
        "shape_name": "Проектна форма от случай",
        "reason": "Липсва MASTER форма",
        "save_training_example": False,
        "geometry": {
            "handles": ["9C6", "9C7"],
            "entity_types": ["LINE", "LINE"],
            "layers": ["ARM-GORNA"],
            "points": [list(т) for т in ТОЧКИ],
            "closed": False,
            "connected": True,
            "physical_connected": True,
            "branched": False,
            "segment_count": 2,
            "segment_lengths_cm": [100.0, 50.0],
            "turn_angles_deg": [-90.0],
            "total_length_cm": 150.0,
            "canonical_signature": "O|n=2|r=0.33333,0.66667|a=-90.00",
            "component_count": 1,
            "inferred_composite": False,
            "near_gap_healed": False,
            "training_eligible": True,
            "warnings": [],
        },
        "dimensions": {
            "source": "ANNOTATION",
            "production_length_cm": 150.0,
            "production_segment_lengths_cm": [],
            "measured_geometry_length_cm": 150.0,
        },
        "annotation": {
            "handle": "9C5",
            "text": "8N10x150",
            "count": 8,
            "diameter_mm": 10,
            "declared_length_cm": 150.0,
        },
    }


def test_proektnata_forma_minava_ot_geometriya_do_razpoznavane(tmp_path: Path):
    # --- 1. Кандидатът на проекта, както го пише прозорецът ------------------ #
    пакет = build_project_shape_candidate(
        _товар(),
        project_name="ВЕРИГА",
        created_at="2026-09-29T20:00:00+03:00",
    )
    кандидат = пакет["candidate"]
    assert кандидат["never_publish_automatically"] is True
    assert кандидат["engine_studio_import"]["ready"] is True
    път = write_project_shape_candidate(tmp_path / "working", пакет)
    записано = json.loads(път.read_text(encoding="utf-8"))
    assert записано["candidate"]["candidate_id"] == кандидат["candidate_id"]

    # --- 2. Черновата: внасянето НИКОГА не дава готова форма ----------------- #
    внесени = import_shape_draft_json(път)
    assert len(внесени) == 1
    форма = внесени[0]
    assert форма.state == "DRAFT"
    assert форма.locked is False
    assert форма.mirror_allowed is False
    код = форма.code

    # --- 3. Контролният тест ------------------------------------------------- #
    хранилище = EngineRepository(tmp_path / "registry")
    чернова = хранилище.clone_current_to_draft("0.0.1", "Александър")
    чернова.shapes.append(форма)
    размери = "; ".join(
        "L%d=%g" % (и + 1, дължина)
        for и, дължина in enumerate((100.0, 50.0))
    )
    форма.tests = [build_control_test(форма, размери, test_id="%s-01" % код)]
    хранилище.save_draft(чернова)

    резултати = [р for р in run_engine_tests(чернова) if р.shape_code == код]
    assert резултати, "контролният тест не е изпълнен за новата форма"
    assert all(р.passed for р in резултати), [р.message for р in резултати]

    # --- 4. Одобрение -------------------------------------------------------- #
    with pytest.raises(EngineRepositoryError):
        # Без одобрил няма одобрение — стъпката не се прескача.
        хранилище.approve_shape(чернова, код, approved_by="  ", note="без име")
    хранилище.approve_shape(чернова, код, approved_by="Александър",
                            note="Проектна форма от случай")
    assert чернова.find_shape(код).state == "APPROVED"

    # --- 5. Публикуване: само с контролната фраза ---------------------------- #
    with pytest.raises(EngineRepositoryError):
        хранилище.publish(чернова, approved_by="Александър",
                          approval_note="без фраза", phrase="друго")
    хранилище.publish(чернова, approved_by="Александър",
                      approval_note="Нова проектна форма %s" % код,
                      phrase=APPROVAL_PHRASE, activate=True)

    публикуван = хранилище.load_current()
    assert публикуван.version == "0.0.1"
    публикувана_форма = публикуван.find_shape(код)
    assert публикувана_форма is not None
    assert публикувана_форма.state == "LOCKED"
    assert публикувана_форма.locked is True

    # --- 6. Разпознаването: същият индекс, който анализът строи -------------- #
    индекс = EngineShapeIndex.from_engine(публикуван)
    assert индекс.find(код) is not None
    запитване = индекс.query_points(ТОЧКИ, closed=False)
    кодове = [съвпадение.entry.code for съвпадение in запитване.matches]
    assert кодове, "индексът не върна нито едно съвпадение за същите върхове"
    assert код in кодове, (
        "публикуваната проектна форма не се намира от индекса на разпознаването: %s"
        % кодове
    )
