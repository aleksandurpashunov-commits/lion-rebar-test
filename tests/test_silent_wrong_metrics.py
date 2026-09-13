"""Автоматична 'silent wrong' метрика (Фаза 1, т.5).

Заменя ръчния одит от 09.09.2026 (2490 реални позиции, преброени на ръка),
който намери, че 97.7% от бройката идваше от суров текст заради непазен
``production_count`` в AUTO_BATCH_GATE връзките. Тези тестове доказват, че
същата класификация вече се изчислява автоматично.

Чете директно ``approved_geometry_links.json`` -- НЕ Final Control ledger-а
-- защото се изпълнява веднага след пакетния анализ (batch_geometry_worker),
много преди операторът изобщо да е пуснал Финален контрол.
"""

from __future__ import annotations

import json
from pathlib import Path

from lion_rebar.export.silent_wrong_metrics import (
    compute_silent_wrong_metrics,
    read_silent_wrong_metrics,
    write_silent_wrong_metrics,
)
from lion_rebar.project.integrity import reconcile_project_integrity

from test_recovery_6_3_1_data_integrity import _current_project, _engine  # type: ignore


def test_unavailable_without_approved_links(tmp_path: Path) -> None:
    metrics = compute_silent_wrong_metrics(tmp_path, _engine())
    assert metrics["available"] is False


def test_link_backed_position_is_classified_safe(tmp_path: Path) -> None:
    _manager, project, _identity, _entities, _report_path = _current_project(tmp_path)

    metrics = compute_silent_wrong_metrics(project.root, _engine())

    assert metrics["available"] is True
    assert metrics["total_approved_positions"] == 1
    assert metrics["count_source"] == {"link.production_count": 1}
    assert metrics["segment_source"] == {"F001_no_breakdown": 1}
    assert metrics["silent_wrong_risk_count"] == 0
    assert metrics["silent_wrong_risk_handles"] == []


def test_missing_production_count_is_flagged_as_risky(tmp_path: Path) -> None:
    """Точно дефектът, който произходният ръчен одит намери: AUTO_BATCH_GATE
    връзка без ``production_count``/``annotation_count`` пада на суров текст."""

    _manager, project, _identity, _entities, _report_path = _current_project(tmp_path)
    links_path = Path(project.root) / "working" / "approved_geometry_links.json"
    payload = json.loads(links_path.read_text(encoding="utf-8"))
    for link in payload["links"]:
        link.pop("production_count", None)
        link.pop("annotation_count", None)
    links_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    metrics = compute_silent_wrong_metrics(project.root, _engine())

    assert metrics["count_source"] == {"parsed_from_raw_text": 1}
    assert metrics["silent_wrong_risk_count"] == 1
    assert metrics["silent_wrong_risk_handles"] == ["T1"]


def test_write_and_read_round_trip(tmp_path: Path) -> None:
    _manager, project, _identity, _entities, _report_path = _current_project(tmp_path)

    written_path = write_silent_wrong_metrics(project.root, _engine())
    assert written_path.is_file()

    loaded = read_silent_wrong_metrics(project.root)
    assert loaded is not None
    assert loaded["available"] is True
    assert loaded["total_approved_positions"] == 1


def test_read_returns_none_when_never_computed(tmp_path: Path) -> None:
    assert read_silent_wrong_metrics(tmp_path) is None


def test_reconcile_project_integrity_surfaces_precomputed_metrics(tmp_path: Path) -> None:
    """Финалният контрол не пресмята метриката сам — чете вече записаната,
    ако batch worker-ът я е изчислил, и никога не блокира при липса."""

    _manager, project, _identity, _entities, report_path = _current_project(tmp_path)
    write_silent_wrong_metrics(project.root, _engine())

    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)

    assert result.silent_wrong_metrics.get("available") is True
    assert result.silent_wrong_metrics.get("total_approved_positions") == 1
    assert result.to_dict()["silent_wrong_metrics"]["available"] is True


def test_reconcile_project_integrity_defaults_to_empty_without_metrics(tmp_path: Path) -> None:
    _manager, project, _identity, _entities, report_path = _current_project(tmp_path)
    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)
    assert result.silent_wrong_metrics == {}
