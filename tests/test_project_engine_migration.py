from __future__ import annotations

import json
from pathlib import Path

import pytest

from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.project.manager import ProjectManager


def test_known_engine_migration_keeps_backup_and_reopens_f003_f005_only(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    old, _manifest, _names = inspect_engine_bundle(
        root
        / "engine_updates"
        / "lion-rebar-master_v0.3.12_POSITION_KEY_F001_CALIBRATION_PRODUCTION_DRAFT.lionengine"
    )
    new, _manifest, _names = inspect_engine_bundle(
        root
        / "engine_updates"
        / "lion-rebar-master_v0.3.13_F003_F005_DISTINCT_CONTEXT_PRODUCTION_DRAFT.lionengine"
    )
    manager = ProjectManager()
    project = manager.create_project(tmp_path, "Migration test", old)
    manager.save_engine_snapshot(project, old)

    approvals = Path(project.root) / "working" / "approved_geometry_links.json"
    approvals.write_text(
        json.dumps(
            {
                "schema": "lion-rebar-approved-links/v1",
                "links": [
                    {"annotation_handle": "A", "master_shape_code": "F003"},
                    {"annotation_handle": "B", "master_shape_code": "F005"},
                    {"annotation_handle": "C", "master_shape_code": "F004"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    returned = manager.migrate_known_engine_snapshot(
        project,
        new,
        allowed_from_versions={"0.3.12"},
        reason="F003/F005 distinct named-context correction",
    )

    assert returned == 2
    assert project.engine_version == "0.3.13"
    payload = json.loads(approvals.read_text(encoding="utf-8"))
    assert [link["annotation_handle"] for link in payload["links"]] == ["C"]
    assert any((Path(project.root) / "master" / "migration_backups").rglob("project.json"))
    assert (Path(project.root) / project.extensions["engine_snapshot"]).is_file()


@pytest.mark.parametrize(
    "source_bundle",
    [
        "lion-rebar-master_v0.3.12_POSITION_KEY_F001_CALIBRATION_PRODUCTION_DRAFT.lionengine",
        "lion-rebar-master_v0.3.13_F003_F005_DISTINCT_CONTEXT_PRODUCTION_DRAFT.lionengine",
    ],
)
def test_v0314_migrates_both_supported_predecessors(
    tmp_path: Path,
    source_bundle: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    old, _manifest, _names = inspect_engine_bundle(
        root / "engine_updates" / source_bundle
    )
    new, _manifest, _names = inspect_engine_bundle(
        root
        / "engine_updates"
        / "lion-rebar-master_v0.3.14_F005_INDEPENDENT_ARMS_PRODUCTION_DRAFT.lionengine"
    )
    manager = ProjectManager()
    project = manager.create_project(tmp_path, f"Migration {old.version}", old)
    manager.save_engine_snapshot(project, old)

    returned = manager.migrate_known_engine_snapshot(
        project,
        new,
        allowed_from_versions={"0.3.12", "0.3.13"},
        reason=(
            f"Engine v{old.version} to v0.3.14: F005 independent arms "
            "and two-sided U relation correction"
        ),
    )

    assert returned == 0
    assert project.engine_version == "0.3.14"
    migration = project.extensions["engine_migrations"][-1]
    assert migration["from_version"] == old.version
    assert migration["to_version"] == "0.3.14"
    assert f"v{old.version}" in migration["reason"]


def test_v0315_returns_only_f010_approvals_to_review(tmp_path: Path) -> None:
    """Преминаването към v0.3.15 връща за преглед само формата, чиято
    САМОЛИЧНОСТ се сменя.

    Отвореното петсегментно стреме излизаше като F010 („НЕСТАНДАРТНА – ДОЛНА
    СКАРА“, чертеж през общия резервен рисувач), а вярната форма е F009 —
    нейният контролен тест ``T-F009-001`` е точно този прът
    (8+79+15+79+8 = 189 cm). Решението на инженера е било взето върху друга
    форма, затова точно тези одобрения се връщат, а останалите се пазят.
    """

    root = Path(__file__).resolve().parents[1]
    old, _manifest, _names = inspect_engine_bundle(
        root
        / "engine_updates"
        / "lion-rebar-master_v0.3.14_F005_INDEPENDENT_ARMS_PRODUCTION_DRAFT.lionengine"
    )
    new, _manifest, _names = inspect_engine_bundle(
        root
        / "engine_updates"
        / "lion-rebar-master_v0.3.15_F009_OPEN_STIRRUP_IDENTITY_PRODUCTION_DRAFT.lionengine"
    )
    manager = ProjectManager()
    project = manager.create_project(tmp_path, "Migration 0.3.15", old)
    manager.save_engine_snapshot(project, old)

    approvals = Path(project.root) / "working" / "approved_geometry_links.json"
    approvals.write_text(
        json.dumps(
            {
                "schema": "lion-rebar-approved-links/v1",
                "links": [
                    {"annotation_handle": "БОРД", "master_shape_code": "F010"},
                    {"annotation_handle": "КАПАНДУРА", "master_shape_code": "F010"},
                    {"annotation_handle": "СТРЕМЕ", "master_shape_code": "F006"},
                    {"annotation_handle": "ЕСОВЕ", "master_shape_code": "F007"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    returned = manager.migrate_known_engine_snapshot(
        project,
        new,
        allowed_from_versions={"0.3.14"},
        reason="Engine v0.3.14 to v0.3.15: F009 open five-segment stirrup identity",
        return_to_review_shape_codes={"F010"},
    )

    assert returned == 2
    assert project.engine_version == "0.3.15"
    payload = json.loads(approvals.read_text(encoding="utf-8"))
    assert [link["annotation_handle"] for link in payload["links"]] == ["СТРЕМЕ", "ЕСОВЕ"]
    assert payload["engine_migration"]["to_version"] == "0.3.15"
    assert any((Path(project.root) / "master" / "migration_backups").rglob("project.json"))


def test_известните_корекции_имат_по_един_път_за_версия() -> None:
    """Таблицата в прозореца е еднозначна: за (активен engine, проект) — една
    корекция, с изрично изброените форми за нов преглед."""

    from lion_rebar.ui.main_window import _KNOWN_ENGINE_CORRECTIONS

    pairs = [
        (item.to_version, from_version)
        for item in _KNOWN_ENGINE_CORRECTIONS
        for from_version in item.from_versions
    ]
    assert len(pairs) == len(set(pairs))

    matching = [
        item
        for item in _KNOWN_ENGINE_CORRECTIONS
        if item.to_version == "0.3.15" and "0.3.14" in item.from_versions
    ]
    assert len(matching) == 1
    assert matching[0].review_shape_codes == frozenset({"F010"})
    # Старата поправка остава непокътната.
    previous = [
        item for item in _KNOWN_ENGINE_CORRECTIONS if item.to_version == "0.3.14"
    ]
    assert len(previous) == 1
    assert previous[0].review_shape_codes == frozenset({"F003", "F005"})
    assert previous[0].from_versions == frozenset({"0.3.12", "0.3.13"})
