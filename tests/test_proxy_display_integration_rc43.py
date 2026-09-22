from __future__ import annotations

import json
import os
import shutil
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report
from lion_rebar.dxf.importer import DxfImporter
from lion_rebar.dxf.proxy_adapter import (
    PROXY_ANNOTATION_SOURCE,
    PROXY_GEOMETRY_SOURCE,
)
from lion_rebar.dxf.proxy_graphics import PROXY_DISPLAY_SOURCE
from lion_rebar.engine.models import EnginePackage
from lion_rebar.export.excel_export import _formula_mass_kg
from lion_rebar.project.data_identity import current_identity
from lion_rebar.project.manager import ProjectManager


_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SHAIBI_DXF = Path(
    r"C:\Users\Александър\OneDrive\Работен плот\DXF проекти"
    r"\39_83_shaibi_+21.48+24.78  do +24.78+28.08.dxf"
)
_DEFAULT_R10_ROOT = _WORKSPACE_ROOT / "verification_projects" / "rc41_r10_replay"
_DEFAULT_R11_ROOT = _WORKSPACE_ROOT / "verification_projects" / "rc41_r11_replay"
_DECISION_FILENAMES = (
    "approved_geometry_links.json",
    "manual_corrections.json",
    "review_center_state.json",
)


def _fixture_path(env_name: str, default: Path, *legacy_env_names: str) -> Path:
    for name in (env_name, *legacy_env_names):
        configured = os.environ.get(name)
        if configured:
            return Path(configured).expanduser()
    return default


def _require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        pytest.skip(f"{label} is unavailable: {path}")
    return path


def _require_replay_root(path: Path, label: str) -> Path:
    if path.name.casefold() == "project.json":
        path = path.parent.parent
    required = (
        path / "source" / "original.dxf",
        path / "working" / "project.json",
        path / "working" / "approved_geometry_links.json",
    )
    missing = [str(candidate) for candidate in required if not candidate.is_file()]
    if missing:
        pytest.skip(f"{label} replay is unavailable; missing: {', '.join(missing)}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _approval_links(project_root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    payload = _read_json(project_root / "working" / "approved_geometry_links.json")
    links = {
        str(row["annotation_handle"]): row
        for row in payload.get("links", [])
        if isinstance(row, dict) and row.get("annotation_handle")
    }
    return payload, links


def _engine(project_root: Path) -> EnginePackage:
    candidates = sorted((project_root / "master").glob("engine_*.json"))
    if not candidates:
        pytest.skip(f"ENGINE snapshot is unavailable in {project_root / 'master'}")
    return EnginePackage.from_dict(_read_json(candidates[-1]))


def _project_identity(project_root: Path) -> dict[str, Any]:
    project_payload = _read_json(project_root / "working" / "project.json")
    identity = (project_payload.get("extensions") or {}).get("data_identity")
    assert isinstance(identity, dict), "Replay project must carry its frozen data identity."
    return identity


def _source(record: Any) -> str:
    raw = record.raw
    return str(raw.get("source") or "") if isinstance(raw, Mapping) else ""


def _payload_source(record: Mapping[str, Any]) -> str:
    raw = record.get("raw")
    return str(raw.get("source") or "") if isinstance(raw, Mapping) else ""


def _display_id(record: Any) -> str:
    return str(record.raw.get("display_id") or record.handle)


def _decision_bytes(project_root: Path) -> dict[str, bytes]:
    return {
        filename: (project_root / "working" / filename).read_bytes()
        for filename in _DECISION_FILENAMES
        if (project_root / "working" / filename).is_file()
    }


@pytest.fixture(scope="module")
def shaibi_import():
    source = _fixture_path(
        "LION_REBAR_PROXY_TEST_DXF",
        _DEFAULT_SHAIBI_DXF,
        "LION_REBAR_PROXY_DISPLAY_TEST_DXF",
    )
    return DxfImporter().import_file(_require_file(source, "Confirmed shaibi proxy DXF"))


@pytest.fixture(scope="module")
def r10_replay():
    root = _require_replay_root(
        _fixture_path("LION_REBAR_R10_PROJECT", _DEFAULT_R10_ROOT),
        "r-10",
    )
    return root, DxfImporter().import_file(root / "source" / "original.dxf")


@pytest.fixture(scope="module")
def r11_replay():
    root = _require_replay_root(
        _fixture_path("LION_REBAR_R11_PROJECT", _DEFAULT_R11_ROOT),
        "r-11",
    )
    return root, DxfImporter().import_file(root / "source" / "original.dxf")


def test_confirmed_shaibi_import_now_recognizes_engineering_annotations(
    shaibi_import,
) -> None:
    """2026-09-10: superseded ``test_confirmed_shaibi_import_keeps_display_
    channel_separate_and_stable``. The owner explicitly revisited rc43's
    "arm_shaibi stays display-only" choice — see
    ``ProxyAdapterConfig.source_layers`` docstring — once the same
    duplicate-geometry unreliability that motivated it was addressed by the
    declared-length fallback (``PROXY_DECLARED_LENGTH_CODE``). This real
    fixture's 6 real annotations all now resolve. The display/engineering
    channel separation itself is unchanged in principle: recognized proxies'
    own primitives are suppressed from the generic display channel (the
    documented importer/viewer dedupe) instead of leaking into both.
    """

    result = shaibi_import
    summary = result.summary

    assert len(result.entities) == 1575
    assert summary.proxy_entity_count == 341
    assert summary.proxy_geometry_record_count == 56
    assert summary.proxy_parsed_annotation_count == 6
    assert summary.proxy_resolved_annotation_count == 6

    original_proxies = [
        record
        for record in result.entities
        if record.entity_type.upper() == "ACAD_PROXY_ENTITY"
    ]
    assert len(original_proxies) == 341
    assert sum(record.layer == "arm_shaibi" for record in original_proxies) == 335
    assert all(record.geometry.get("unsupported_for_render") is True for record in original_proxies)
    assert any(
        _source(record) in {PROXY_GEOMETRY_SOURCE, PROXY_ANNOTATION_SOURCE}
        for record in result.entities
    )

    # 99 primitives belong to the now-recognized proxies and are suppressed
    # from the display channel (documented dedupe) instead of double-drawn.
    assert len(result.display_entities) == 3704
    assert summary.proxy_display_instance_count == 341
    assert summary.proxy_display_discovered_primitive_count == 3803
    assert summary.proxy_display_rendered_primitive_count == 3704
    assert summary.proxy_display_suppressed_duplicate_count == 99
    assert summary.proxy_display_unsupported_primitive_count == 0
    assert summary.proxy_display_complete_count == 341
    assert summary.proxy_display_partial_count == 0
    assert summary.proxy_display_missing_graphic_count == 0
    assert summary.proxy_display_unreadable_count == 0
    assert summary.proxy_display_type_counts == {
        "ARC": 439,
        "CIRCLE": 708,
        "POLYLINE": 1171,
        "TEXT": 1485,
    }
    assert any(
        record.layer not in {"GA_X", "GA_Y"} for record in result.display_entities
    )
    assert all(_source(record) == PROXY_DISPLAY_SOURCE for record in result.display_entities)
    assert all(record.raw.get("engineering_eligible") is False for record in result.display_entities)
    assert not any(_source(record) == PROXY_DISPLAY_SOURCE for record in result.entities)
    display_ids = [_display_id(record) for record in result.display_entities]
    assert len(display_ids) == len(set(display_ids)) == 3704

    first_payload = result.to_dict()
    second_payload = result.to_dict()
    assert first_payload == second_payload
    assert len(first_payload["entities"]) == 1575
    assert len(first_payload["display_entities"]) == 3704
    assert not any(
        _payload_source(record) == PROXY_DISPLAY_SOURCE
        for record in first_payload["entities"]
    )
    assert all(
        _payload_source(record) == PROXY_DISPLAY_SOURCE
        for record in first_payload["display_entities"]
    )


def test_r10_strict_engineering_and_batch_quantities_survive_display_dedup(
    r10_replay,
    tmp_path: Path,
) -> None:
    root, result = r10_replay
    summary = result.summary

    assert summary.proxy_entity_count == 298
    assert summary.proxy_geometry_record_count == 198
    assert summary.proxy_parsed_annotation_count == 130
    assert summary.proxy_resolved_annotation_count == 130
    assert summary.proxy_display_instance_count == 298
    assert summary.proxy_display_discovered_primitive_count == 2712
    assert summary.proxy_display_rendered_primitive_count == 1491
    assert summary.proxy_display_suppressed_duplicate_count == 1221
    assert summary.proxy_display_unsupported_primitive_count == 0
    # 1751 -> 2504 на 22.09.2026: импортът вече разгъва съдържанието на
    # блоковете (``DxfImporter._exploded_block_records``). Дотук ``INSERT`` се
    # записваше като ЕДИН запис и геометрията вътре не съществуваше за
    # анализа — собственикът я нарече „невидимите геометрии“. Числото расте с
    # точно толкова, колкото са обектите в блоковете; всичко останало по този
    # чертеж е непроменено, затова съседните твърдения тук стоят както са.
    assert len(result.entities) == 2504
    от_блок = [r for r in result.entities if (r.raw or {}).get("source_insert_handle")]
    assert len(от_блок) == 2504 - 1751
    assert len(result.display_entities) == 1491

    strict_geometry = [
        record for record in result.entities if _source(record) == PROXY_GEOMETRY_SOURCE
    ]
    strict_annotations = [
        record for record in result.entities if _source(record) == PROXY_ANNOTATION_SOURCE
    ]
    assert len(strict_geometry) == 198
    assert len(strict_annotations) == 130

    strict_source_keys = {
        str(record.raw["proxy_source_key"])
        for record in strict_geometry
    }
    for record in strict_annotations:
        strict_source_keys.update(
            str(value) for value in record.raw.get("proxy_text_source_keys", [])
        )
    display_source_keys = {
        str(record.raw["proxy_source_key"]) for record in result.display_entities
    }
    assert len(strict_source_keys) == 1221
    assert len(display_source_keys) == 1491
    assert strict_source_keys.isdisjoint(display_source_keys)

    _approval_payload, approved_links = _approval_links(root)
    engine = _engine(root)
    report = build_batch_geometry_report(
        result.entities,
        tmp_path,
        project_name="R10_RC43_PROXY_DISPLAY_REGRESSION",
        approved_links=approved_links,
        generated_at=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=engine.settings,
        data_identity=_project_identity(root),
        checkpoint_every=1000,
    )
    payload = _read_json(report.json_path)
    actionable_rows = [
        row for row in payload["rows"] if row.get("annotation_class") == "ACTIONABLE"
    ]

    assert report.total_annotations == report.processed_annotations == 136
    assert report.actionable_count == 132
    assert report.approved_count == 130
    assert report.proxy_parent_provenance_count == 130
    assert report.risk_count == 2
    assert report.context_count == 4
    assert report.error_count == 0
    assert len(actionable_rows) == 132
    assert sum(int(row.get("annotation_count") or 0) for row in actionable_rows) == 2539
    assert len(approved_links) == 130
    assert sum(int(row["annotation_count"]) for row in approved_links.values()) == 2535
    assert not any(_source(record) == PROXY_DISPLAY_SOURCE for record in result.entities)


def test_r11_manual_approvals_counts_and_mass_remain_frozen(
    r11_replay,
    tmp_path: Path,
) -> None:
    root, result = r11_replay
    before_decisions = _decision_bytes(root)
    _approval_payload, approved_links = _approval_links(root)
    engine = _engine(root)

    report = build_batch_geometry_report(
        result.entities,
        tmp_path,
        project_name="R11_RC43_PROXY_DISPLAY_REGRESSION",
        approved_links=approved_links,
        generated_at=datetime(2026, 9, 8, 12, 1, tzinfo=timezone.utc),
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=engine.settings,
        data_identity=_project_identity(root),
        checkpoint_every=1000,
    )
    payload = _read_json(report.json_path)
    rows = payload["rows"]
    expected_handles = {
        "CB6::BJ-FI-L",
        "CBD::BJ-FI-L",
        "CF8::BJ-FI-L",
        "CFE::BJ-FI-L",
        "D21::BJ-FI-L",
        "D32::BJ-FI-L",
        "1064::BJ-FI-L",
        "1072::BJ-FI-L",
        "1082::BJ-FI-L",
    }

    assert len(approved_links) == 9
    assert set(approved_links) == expected_handles
    assert report.total_annotations == report.processed_annotations == 9
    assert report.error_count == 0
    assert len(rows) == 9
    assert {str(row["annotation_handle"]) for row in rows} == expected_handles
    assert all(row.get("approved_in_project") is True for row in rows)
    assert Counter(str(row.get("master_shape_code") or "") for row in rows) == {
        "F001": 3,
        "F003": 1,
        "F006": 3,
        "F007": 2,
    }
    assert sum(int(row["annotation_count"]) for row in rows) == 4584
    assert sum(int(row["annotation_count"]) for row in approved_links.values()) == 4584

    frozen_mass_kg = sum(
        _formula_mass_kg(
            SimpleNamespace(
                diameter_mm=row["diameter_mm"],
                production_length_cm=row["production_length_cm"],
            ),
            int(row["annotation_count"]),
        )
        for row in approved_links.values()
    )
    assert frozen_mass_kg == pytest.approx(5877.590902748, rel=1e-12, abs=1e-9)
    assert _decision_bytes(root) == before_decisions


def test_r10_project_reopen_replaces_cache_without_accumulating_display_records(
    r10_replay,
    tmp_path: Path,
) -> None:
    source_root, first_import = r10_replay
    copied_root = tmp_path / "r10_reopen"
    (copied_root / "source").mkdir(parents=True)
    (copied_root / "working").mkdir(parents=True)
    shutil.copy2(source_root / "source" / "original.dxf", copied_root / "source" / "original.dxf")
    shutil.copy2(source_root / "working" / "project.json", copied_root / "working" / "project.json")
    for filename in _DECISION_FILENAMES:
        source = source_root / "working" / filename
        if source.is_file():
            shutil.copy2(source, copied_root / "working" / filename)

    before_decisions = _decision_bytes(copied_root)
    manager = ProjectManager()
    project = manager.open_project(copied_root)
    before_identity = current_identity(project)
    cache_path = manager.save_extracted_entities(project, first_import.to_dict())
    first_cache_bytes = cache_path.read_bytes()

    reopened = manager.open_project(copied_root)
    assert current_identity(reopened) == before_identity
    second_import = DxfImporter().import_file(copied_root / "source" / "original.dxf")
    second_cache_path = manager.save_extracted_entities(reopened, second_import.to_dict())
    second_cache_bytes = second_cache_path.read_bytes()

    first_display_ids = [_display_id(record) for record in first_import.display_entities]
    second_display_ids = [_display_id(record) for record in second_import.display_entities]
    assert first_import.to_dict() == second_import.to_dict()
    assert first_display_ids == second_display_ids
    assert len(second_display_ids) == len(set(second_display_ids)) == 1491
    assert len(second_import.entities) == 2504
    assert first_cache_bytes == second_cache_bytes
    cached_payload = _read_json(second_cache_path)
    assert len(cached_payload["entities"]) == 2504
    assert len(cached_payload["display_entities"]) == 1491
    assert cached_payload["data_identity"] == before_identity.to_dict()
    assert _decision_bytes(copied_root) == before_decisions
