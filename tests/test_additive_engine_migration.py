"""Проект се пренася към нов engine САМО когато той единствено добавя форми.

Собственик, 22.09.2026: „искам, когато направя нова форма, да може да се
разпознава или поне избира в този чертеж, а на следващия вече да си я има
одобрена“.

Закотвянето пази нещо само когато формата, върху която е взето старо решение,
се е променила. Когато новият engine не променя нищо, а само добавя, нито едно
одобрение не може да смени смисъла си. Условието е ПРОВЕРИМО, не преценка.
"""
import json
from pathlib import Path

import pytest

from lion_rebar.engine.additive_upgrade import compare_for_additive_upgrade
from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.project.manager import ProjectManager


def _форма(code: str, name: str, дължина: str = "L1") -> ShapeDefinition:
    return ShapeDefinition(
        code=code, name=name, state="LOCKED", locked=True, active=True,
        segments=[SegmentDefinition(index=1, length_code=дължина)],
    )


def _engine(version: str, форми) -> EnginePackage:
    return EnginePackage(
        engine_id="lion-rebar-master", title="MASTER", version=version,
        state="PUBLISHED", shapes=list(форми),
    )


def test_само_добавена_форма_е_добавка():
    старо = _engine("0.3.20", [_форма("F001", "ПРАВ ПРЪТ")])
    ново = _engine("0.3.21", [_форма("F001", "ПРАВ ПРЪТ"), _форма("F021", "Кобилица-1")])

    изход = compare_for_additive_upgrade(старо, ново)

    assert изход.is_additive
    assert изход.added_shape_codes == ("F021",)


def test_променена_форма_НЕ_е_добавка():
    """Дори едно различие в стара форма спира пренасянето."""

    старо = _engine("0.3.20", [_форма("F001", "ПРАВ ПРЪТ")])
    ново = _engine("0.3.21", [_форма("F001", "ПРАВ ПРЪТ", дължина="L2"),
                              _форма("F021", "Кобилица-1")])

    изход = compare_for_additive_upgrade(старо, ново)

    assert not изход.is_additive
    assert "F001" in изход.blocked_reason


def test_изчезнала_форма_НЕ_е_добавка():
    старо = _engine("0.3.20", [_форма("F001", "ПРАВ"), _форма("F002", "РЕДУКЦИЯ")])
    ново = _engine("0.3.21", [_форма("F001", "ПРАВ")])

    assert not compare_for_additive_upgrade(старо, ново).is_additive


def test_променено_правило_НЕ_е_добавка():
    """Не само формите: всичко, което влияе на разпознаването."""

    старо = _engine("0.3.20", [_форма("F001", "ПРАВ")])
    ново = _engine("0.3.21", [_форма("F001", "ПРАВ"), _форма("F021", "Нова")])
    ново.settings = {"някаква_настройка": 1}

    assert not compare_for_additive_upgrade(старо, ново).is_additive


def test_нов_ключ_с_подразбираща_стойност_НЕ_спира_добавката():
    """Речникът на разпознаването расте; старият пакет просто няма ключа.

    Измерено 22.09.2026: „тест-001“ не можеше да мине от v0.3.15 към v0.3.20
    заради ЕДИН добавен ключ — ``declared_rebar_geometry_layers: []``, празен
    списък, който изключва собственото си правило. Двете нови форми не
    стигаха до чертежа заради настройка, която не прави нищо.

    ``RecognitionPolicy.from_settings`` попълва всеки липсващ ключ с неговото
    подразбиране, тоест липсващият ключ Е подразбирането — а не „каквото и да
    е“. Затова сравнението минава през политиката, не дума по дума.
    """

    старо = _engine("0.3.15", [_форма("F001", "ПРАВ")])
    старо.settings = {"geometry_recognition": {"angle_match_tolerance_deg": 8.0}}
    ново = _engine("0.3.20", [_форма("F001", "ПРАВ"), _форма("F020", "Кобилица")])
    ново.settings = {
        "geometry_recognition": {
            "angle_match_tolerance_deg": 8.0,
            "declared_rebar_geometry_layers": [],
        }
    }

    изход = compare_for_additive_upgrade(старо, ново)

    assert изход.is_additive
    assert изход.added_shape_codes == ("F020",)


def test_нов_ключ_с_РЕАЛНА_стойност_спира_добавката():
    """Разхлабва се само празното. Стойност, различна от подразбирането,
    мени разпознаването и проектът остава на своя snapshot."""

    старо = _engine("0.3.15", [_форма("F001", "ПРАВ")])
    старо.settings = {"geometry_recognition": {}}
    ново = _engine("0.3.20", [_форма("F001", "ПРАВ"), _форма("F020", "Кобилица")])
    ново.settings = {
        "geometry_recognition": {"declared_rebar_geometry_layers": ["s-Reinf"]}
    }

    изход = compare_for_additive_upgrade(старо, ново)

    assert not изход.is_additive
    assert "settings" in изход.blocked_reason


def test_променена_стойност_на_стар_ключ_спира_добавката():
    старо = _engine("0.3.15", [_форма("F001", "ПРАВ")])
    старо.settings = {"geometry_recognition": {"angle_match_tolerance_deg": 8.0}}
    ново = _engine("0.3.20", [_форма("F001", "ПРАВ"), _форма("F020", "Кобилица")])
    ново.settings = {"geometry_recognition": {"angle_match_tolerance_deg": 12.0}}

    изход = compare_for_additive_upgrade(старо, ново)

    assert not изход.is_additive
    assert "settings" in изход.blocked_reason


def test_настройка_извън_разпознаването_се_сравнява_дословно():
    """Само ``geometry_recognition`` минава през политиката."""

    старо = _engine("0.3.15", [_форма("F001", "ПРАВ")])
    старо.settings = {"max_segments": 16}
    ново = _engine("0.3.20", [_форма("F001", "ПРАВ"), _форма("F020", "Кобилица")])
    ново.settings = {"max_segments": 24}

    assert not compare_for_additive_upgrade(старо, ново).is_additive


def test_само_нова_версия_без_форми_не_е_добавка():
    старо = _engine("0.3.20", [_форма("F001", "ПРАВ")])
    ново = _engine("0.3.21", [_форма("F001", "ПРАВ")])

    изход = compare_for_additive_upgrade(старо, ново)
    assert not изход.is_additive
    assert "няма добавени" in изход.blocked_reason


def test_одобрението_дописва_бележка_и_това_НЕ_спира_добавката():
    """``approve_shape`` дописва в ``notes`` — това не мени нито една стойност."""

    стара = _форма("F001", "ПРАВ ПРЪТ")
    нова = _форма("F001", "ПРАВ ПРЪТ")
    нова.notes = "Одобрено от Александър: ок"

    изход = compare_for_additive_upgrade(
        _engine("0.3.20", [стара]),
        _engine("0.3.21", [нова, _форма("F021", "Нова")]),
    )
    assert изход.is_additive


def test_проектът_се_пренася_и_нищо_не_се_връща_за_преглед(tmp_path: Path):
    старо = _engine("0.3.20", [_форма("F001", "ПРАВ ПРЪТ")])
    ново = _engine("0.3.21", [_форма("F001", "ПРАВ ПРЪТ"), _форма("F021", "Кобилица-1")])

    m = ProjectManager()
    project = m.create_project(tmp_path, "Добавка", старо)
    m.save_engine_snapshot(project, старо)

    одобрения = Path(project.root) / "working" / "approved_geometry_links.json"
    одобрения.write_text(json.dumps({
        "schema": "lion-rebar-approved-links/v1",
        "links": [{"annotation_handle": "A", "master_shape_code": "F001"}],
    }, ensure_ascii=False), encoding="utf-8")

    добавени = m.migrate_additive_engine(project, ново, by="проба")

    assert добавени == ("F021",)
    assert project.engine_version == "0.3.21"
    # одобрението стои непокътнато
    payload = json.loads(одобрения.read_text(encoding="utf-8"))
    assert len(payload["links"]) == 1
    # следата е вписана
    миграции = project.extensions["engine_migrations"]
    assert миграции[-1]["kind"] == "ADDITIVE"
    assert миграции[-1]["added_shapes"] == ["F021"]
    assert миграции[-1]["returned_to_review"] == 0
    # новата форма вече е в закотвения engine на проекта
    закотвен = m.load_project_engine(project)
    assert закотвен is not None and закотвен.find_shape("F021") is not None


def test_променен_engine_оставя_проекта_на_неговия_snapshot(tmp_path: Path):
    старо = _engine("0.3.20", [_форма("F001", "ПРАВ ПРЪТ")])
    ново = _engine("0.3.21", [_форма("F001", "ПРАВ ПРЪТ", дължина="L2")])

    m = ProjectManager()
    project = m.create_project(tmp_path, "Промяна", старо)
    m.save_engine_snapshot(project, старо)

    assert m.migrate_additive_engine(project, ново) == ()
    assert project.engine_version == "0.3.20"
