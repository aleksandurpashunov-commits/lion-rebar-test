"""Recovery.3: автоматично одобрение по пакетната производствена порта.

Директива на собственика (2026-08-07): само наистина ръчните случаи са ръчни;
всичко, което минава портата, се одобрява само̀, без кликове. Портата НЕ се
разхлабва — ползва се същата ``build_batch_export_context``; ръчните одобрения
никога не се презаписват; всяко автоматично решение носи одитен запис
``AUTO_BATCH_GATE`` и е обратимо (link файлът пази резервно копие).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export import auto_approval
from lion_rebar.export.auto_approval import (
    AUTO_APPROVED_BY,
    AUTO_STATUS,
    auto_approve_gate_positions,
)


def _fake_context(*, passed, blocked, actionable):
    positions = tuple(
        SimpleNamespace(
            annotation_handle=handle,
            geometry_handles=(f"{handle}-G",),
            count=20,
            diameter_mm=8.0,
            production_length_cm=700.0,
            production_length_source="ANNOTATION",
            segment_lengths_cm=(15.0, 670.0, 15.0),
            turn_angles_deg=(90.0, 90.0),
            shape_code="F003",
            shape_name="ПРЪТ С ДВА ВИНКЕЛА",
        )
        for handle in passed
    )
    blockers = tuple(
        SimpleNamespace(
            annotation_handle=handle,
            reason_codes=("MATCHER_REVIEW",),
        )
        for handle in blocked
    )
    return SimpleNamespace(
        positions=positions,
        blockers=blockers,
        actionable_count=actionable,
    )


def _project(tmp_path: Path) -> Path:
    (tmp_path / "working").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _report(tmp_path: Path, handles) -> Path:
    payload = {
        "schema": "lion-rebar-geometry-batch-report/v3",
        "rows": [
            {
                "annotation_handle": handle,
                "annotation_text": "20N8x700",
                "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
                "matcher_disposition": "CONTROL",
                "candidate_confidence": "СИЛЕН ПО ГЕОМЕТРИЯ — Ø ОТ НАДПИСА",
                "candidate_layer": "ARM-N8",
                "master_shape_code": "F003",
                "master_shape_name": "ПРЪТ С ДВА ВИНКЕЛА",
                # Deliberately different raw evidence: the link must persist
                # the already Engine-validated ExportPosition output instead.
                "production_segments_cm": [1.0, 698.0, 1.0],
                "canonical_segment_lengths_cm": [],
            }
            for handle in handles
        ],
    }
    path = tmp_path / "report_geometry_batch.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _links(project: Path) -> list[dict]:
    path = project / "working" / "approved_geometry_links.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["links"]


def test_gate_passing_positions_are_approved_with_audit_trail(tmp_path, monkeypatch):
    project = _project(tmp_path)
    report = _report(tmp_path, ["A1", "A2", "B1"])
    monkeypatch.setattr(
        auto_approval,
        "build_batch_export_context",
        lambda *args, **kwargs: _fake_context(
            passed=["A1", "A2"], blocked=["B1"], actionable=3
        ),
    )
    summary = auto_approve_gate_positions(
        project,
        report_path=report,
        entities=[],
        engine=SimpleNamespace(version="0.3.13"),
    )
    assert not summary.failed
    assert summary.actionable_count == 3
    assert summary.newly_approved_count == 2
    assert summary.blocked_count == 1
    assert summary.blocked_reason_counts == {"MATCHER_REVIEW": 1}
    links = _links(project)
    assert {link["annotation_handle"] for link in links} == {"A1", "A2"}
    for link in links:
        assert link["approval_status"] == AUTO_STATUS
        assert link["approved_by"] == AUTO_APPROVED_BY
        assert link["auto_gate"]["report"] == report.name
        assert link["auto_gate"]["production_dimensions_persisted"] is True
        assert link["master_shape_code"] == "F003"
        assert link["production_segment_lengths_cm"] == [15.0, 670.0, 15.0]
        assert link["production_turn_angles_deg"] == [90.0, 90.0]
    # Блокираният B1 никога не се одобрява автоматично.
    assert all(link["annotation_handle"] != "B1" for link in links)


def test_auto_approved_link_persists_validated_count_and_diameter(tmp_path, monkeypatch):
    """Фаза 1, Стъпка 1 finding: export четеше 97.7% от бройката чрез
    ре-парснат суров текст, защото AUTO_BATCH_GATE връзката никога не пазеше
    ``production_count``/``production_diameter_mm`` — въпреки че
    ``build_batch_export_context`` вече ги е проверило (``count``/
    ``diameter_mm`` на ``position``) по същия гейт като production_length_cm
    и сегментите, които СА се пазели. Без този фикс export трябва да гадае
    от суровия текст на всеки export; при странен/нечетим текст това е тихо
    грешна бройка, не липсваща.
    """

    project = _project(tmp_path)
    report = _report(tmp_path, ["A1"])
    monkeypatch.setattr(
        auto_approval,
        "build_batch_export_context",
        lambda *args, **kwargs: _fake_context(passed=["A1"], blocked=[], actionable=1),
    )
    auto_approve_gate_positions(
        project, report_path=report, entities=[],
        engine=SimpleNamespace(version="0.3.13"),
    )
    link = _links(project)[0]
    assert link["production_count"] == 20
    assert link["production_diameter_mm"] == 8.0


def test_manual_approval_is_never_overwritten_and_rerun_is_idempotent(
    tmp_path, monkeypatch
):
    project = _project(tmp_path)
    report = _report(tmp_path, ["A1", "A2"])
    manual = {
        "schema": "lion-rebar-approved-geometry-links/v1",
        "links": [
            {
                "annotation_handle": "A1",
                "approval_status": "ОДОБРЕНА ВРЪЗКА",
                "approved_by": "USER",
                "geometry_handles": ["MANUAL-G"],
            }
        ],
        "history": [],
    }
    links_path = project / "working" / "approved_geometry_links.json"
    links_path.write_text(json.dumps(manual, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        auto_approval,
        "build_batch_export_context",
        lambda *args, **kwargs: _fake_context(
            passed=["A1", "A2"], blocked=[], actionable=2
        ),
    )
    for _ in range(2):  # повторното изпълнение е идемпотентно
        summary = auto_approve_gate_positions(
            project,
            report_path=report,
            entities=[],
            engine=SimpleNamespace(version="0.3.13"),
        )
        assert summary.kept_manual_count == 1
        assert summary.newly_approved_count == 1
        links = {link["annotation_handle"]: link for link in _links(project)}
        assert len(links) == 2
        assert links["A1"]["approved_by"] == "USER"
        assert links["A1"]["geometry_handles"] == ["MANUAL-G"]
        assert links["A2"]["approved_by"] == AUTO_APPROVED_BY


def test_gate_error_reports_failure_and_writes_nothing(tmp_path):
    project = _project(tmp_path)
    report = _report(tmp_path, ["A1"])
    # Без чертожни рамки реалната порта отказва — не одобряваме нищо.
    summary = auto_approve_gate_positions(
        project,
        report_path=report,
        entities=[
            DxfEntityRecord(
                "G1",
                "LINE",
                "ARM-N8",
                {"start": [0.0, 0.0, 0.0], "end": [100.0, 0.0, 0.0]},
            )
        ],
        engine=SimpleNamespace(version="0.3.13", shapes=(), tests=()),
    )
    assert summary.failed
    assert "рамки" in summary.error
    assert _links(project) == []


def test_batch_worker_no_longer_generates_the_auto_draft_excel():
    """Пакетният анализ вече не създава AUTO_DRAFT Excel (директива).

    Това е статичен source-contract тест и не трябва да import-ва Qt модула.
    Така проверката остава изпълнима и в headless verification среда без
    PySide6, без да отслабва трите първоначални assertions.
    """

    worker_path = (
        Path(__file__).resolve().parents[1]
        / "lion_rebar"
        / "ui"
        / "batch_geometry_worker.py"
    )
    source = worker_path.read_text(encoding="utf-8")
    assert "generate_batch_excel_output" not in source
    assert "auto_approve_gate_positions" in source
    # Worker-ът държи ProjectModel; авто-одобрението иска кореновия път.
    assert "Path(self._project.root)" in source
