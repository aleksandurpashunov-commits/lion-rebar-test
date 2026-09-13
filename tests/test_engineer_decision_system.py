from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import pytest

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.engineer_decision import save_engineer_decision
from lion_rebar.export.excel_export import load_export_context
from lion_rebar.project.integrity import reconcile_project_integrity
from lion_rebar.project.models import ProjectModel
from lion_rebar.review_center_model import final_confirmation_progress
from lion_rebar.session.learning_pack import build_learning_pack


def _engineer_payload(*, handle: str = "A1", geometry: tuple[str, ...] = ("G1",)) -> dict:
    return {
        "schema": "lion-rebar-engineer-decision/v1",
        "action_class": "CORRECTION",
        "reason_code": "WRONG_SHAPE",
        "reason": "Грешна MASTER форма",
        "note": "Фибата е F005 с размери 40+14+40.",
        "annotation": {
            "handle": handle,
            "text": "29N8x94",
            "resolved_text": "31N10x94",
        },
        "original": {
            "count": 29,
            "diameter_mm": 8,
            "declared_length_cm": 94,
            "shape_code": "F003",
            "geometry_length_raw": 93.8,
            "raw_numeric_difference": 0.2,
        },
        "resolved": {
            "count": 31,
            "diameter_mm": 10,
            "production_length_cm": 94,
            "segment_lengths_cm": [40, 14, 40],
            "shape_code": "F005",
            "shape_name": "ФИБА",
        },
        "geometry": {
            "handles": list(geometry),
            "layer": "ARM_Bars",
            "basis": "OWNER_ENGINEER_DECISION",
            "notes": ["owner verified"],
        },
    }


def test_engineer_decision_persists_complete_production_truth(tmp_path: Path) -> None:
    path = save_engineer_decision(tmp_path, _engineer_payload())
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["links"]) == 1
    link = data["links"][0]
    assert link["approved_by"] == "OWNER_ENGINEER_DECISION"
    assert link["approval_status"].startswith("ENGINEER_RESOLVED")
    assert link["annotation_text"] == "31N10x94"
    assert link["source_annotation_text"] == "29N8x94"
    assert link["production_count"] == 31
    assert link["production_diameter_mm"] == 10.0
    assert link["production_length_cm"] == 94.0
    assert link["production_segment_lengths_cm"] == [40.0, 14.0, 40.0]
    assert link["master_shape_code"] == "F005"
    assert link["geometry_handles"] == ["G1"]
    assert link["engineer_decision"]["action_class"] == "CORRECTION"

    replacement = _engineer_payload()
    replacement["resolved"]["count"] = 32
    replacement["annotation"]["resolved_text"] = "32N10x94"
    save_engineer_decision(tmp_path, replacement)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["links"][0]["production_count"] == 32
    assert len(data["history"]) == 1


def test_engineer_decision_preserves_the_prior_diagnostic_problem(tmp_path: Path) -> None:
    payload = _engineer_payload()
    payload["diagnostic_problem"] = {
        "decision": "PROBLEM_RECORDED",
        "reason_code": "WRONG_MASTER",
        "reason": "Геометрията е вярна, но MASTER формата е грешна",
        "correct_specification": "F005, 31N10x94, 40+14+40",
        "note": "Проверено спрямо локалния детайл.",
    }
    path = save_engineer_decision(tmp_path, payload)
    link = json.loads(path.read_text(encoding="utf-8"))["links"][0]
    problem = link["engineer_decision"]["diagnostic_problem"]
    assert problem["reason_code"] == "WRONG_MASTER"
    assert problem["correct_specification"].startswith("F005")


def test_engineer_decision_requires_explicit_no_geometry_reason(tmp_path: Path) -> None:
    payload = _engineer_payload(geometry=())
    with pytest.raises(ValueError, match="без DXF геометрия"):
        save_engineer_decision(tmp_path, payload)
    payload["reason_code"] = "NO_GEOMETRY"
    payload["reason"] = "Липсва или не е необходима отделна геометрия"
    path = save_engineer_decision(tmp_path, payload)
    link = json.loads(path.read_text(encoding="utf-8"))["links"][0]
    assert link["geometry_handles"] == []
    assert link["geometry_optional_by_engineer"] is True


def test_engineer_decision_rejects_non_production_shape_and_wrong_segments(
    tmp_path: Path,
) -> None:
    payload = _engineer_payload()
    payload["resolved"]["shape_code"] = "F013"
    payload["resolved"]["shape_name"] = "СТЪЛБИЩНО РАМО 2"
    with pytest.raises(ValueError, match="производствен MASTER"):
        save_engineer_decision(tmp_path, payload)


def test_engineer_decision_can_reassign_geometry_from_auto_but_not_manual(
    tmp_path: Path,
) -> None:
    working = tmp_path / "working"
    working.mkdir()
    path = working / "approved_geometry_links.json"
    _write_json(
        path,
        {
            "links": [
                {
                    "annotation_handle": "OLD_AUTO",
                    "geometry_handles": ["G1"],
                    "approved_by": "AUTO_BATCH_GATE",
                    "approval_status": "ОДОБРЕНО АВТОМАТИЧНО (ПАКЕТНА ПОРТА)",
                }
            ],
            "history": [],
        },
    )
    # От rc27: отнемането на АВТОМАТИЧНО одобрена геометрия изисква
    # изрично потвърждение. Позицията, която я губи, е била одобрена и
    # би се върнала за преглед без нито дума.
    from lion_rebar.engineer_decision import GeometryTakeoverRequired

    try:
        save_engineer_decision(tmp_path,
                               _engineer_payload(handle="A1", geometry=("G1",)))
    except GeometryTakeoverRequired as conflict:
        assert conflict.losing_annotation == "OLD_AUTO"
    else:
        raise AssertionError("отнемането трябва да иска потвърждение")

    save_engineer_decision(tmp_path,
                           _engineer_payload(handle="A1", geometry=("G1",)),
                           allow_geometry_takeover=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [row["annotation_handle"] for row in data["links"]] == ["A1"]
    assert data["history"][0]["replacement_reason"] == (
        "GEOMETRY_OWNERSHIP_REASSIGNED_BY_ENGINEER"
    )

    _write_json(
        path,
        {
            "links": [
                {
                    "annotation_handle": "OLD_USER",
                    "geometry_handles": ["G1"],
                    "approved_by": "OWNER_ENGINEER_DECISION",
                    "approval_status": "ENGINEER_RESOLVED",
                }
            ],
            "history": [],
        },
    )
    with pytest.raises(ValueError, match="потвърдена ръчно"):
        save_engineer_decision(tmp_path, _engineer_payload(handle="A1", geometry=("G1",)))

    payload = _engineer_payload()
    payload["resolved"]["shape_code"] = "F004"
    payload["resolved"]["shape_name"] = "ПРЪТ С ЕДИН ВИНКЕЛ"
    payload["resolved"]["segment_lengths_cm"] = [40, 14, 40]
    with pytest.raises(ValueError, match="MASTER проверката"):
        save_engineer_decision(tmp_path, payload)


def _safe_row(handle: str) -> dict:
    return {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": handle,
        "annotation_text": "2N12x700",
        "annotation_count": 2,
        "diameter_mm": 12,
        "declared_length_cm": 700,
        "numeric_difference": 0.0,
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "matcher_disposition": "CONTROL",
        "candidate_handles": [f"G-{handle}"],
        "master_shape_code": "F003",
        "master_shape_name": "ПРЪТ С ДВА ВИНКЕЛА",
        "canonical_segment_lengths_cm": [20, 660, 20],
        "data_quality_flags": [],
    }


def _engineering_row(handle: str) -> dict:
    row = _safe_row(handle)
    row.update(
        {
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "matcher_disposition": "MANUAL",
            "geometry_owner_conflict": True,
            "global_conflict": True,
        }
    )
    return row


def test_final_confirmation_never_sweeps_recorded_problem() -> None:
    safe = _safe_row("S1")
    flagged_safe = _safe_row("S2")
    manual = _engineering_row("M1")
    progress = final_confirmation_progress(
        [safe, flagged_safe, manual],
        decisions={"S2": {"decision": "PROBLEM_RECORDED"}},
    )
    assert progress.safe_handles == ("S1",)
    assert set(progress.engineering_handles) == {"S2", "M1"}
    assert progress.ready_for_final_confirmation is False


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def test_integrity_separates_safe_confirmation_from_engineering_work(tmp_path: Path) -> None:
    root = tmp_path / "P"
    working = root / "working"
    report = tmp_path / "report.json"
    _write_json(report, {"rows": [_safe_row("S1"), _engineering_row("M1")]})
    _write_json(working / "approved_geometry_links.json", {"links": [], "history": []})
    _write_json(working / "review_center_state.json", {"decisions": {}})
    result = reconcile_project_integrity(root, report_path=report, apply=True)
    assert result.unresolved_positions == 2
    assert result.safe_confirmation_pending == 1
    assert result.engineering_decisions_pending == 1
    assert result.full_specification_complete is False

    for handle, shape in (("S1", "F003"), ("M1", "F005")):
        payload = _engineer_payload(handle=handle, geometry=(f"G-{handle}",))
        payload["resolved"]["shape_code"] = shape
        payload["resolved"]["shape_name"] = shape
        payload["resolved"]["count"] = 2
        payload["resolved"]["diameter_mm"] = 12
        payload["resolved"]["production_length_cm"] = 700
        payload["resolved"]["segment_lengths_cm"] = [20, 660, 20]
        payload["annotation"]["text"] = "2N12x700"
        payload["annotation"]["resolved_text"] = "2N12x700"
        save_engineer_decision(root, payload)
    result = reconcile_project_integrity(root, report_path=report, apply=True)
    assert result.unresolved_positions == 0
    assert result.safe_confirmation_pending == 0
    assert result.engineering_decisions_pending == 0
    assert result.full_specification_complete is True


def test_export_uses_engineer_resolved_count_diameter_and_text(tmp_path: Path) -> None:
    working = tmp_path / "working"
    working.mkdir()
    _write_json(
        working / "final_integrity_report.json",
        {
            "integrity_passed": True,
            "full_specification_complete": True,
            "duplicate_groups_remaining": 0,
        },
    )
    with (working / "final_integrity_ledger.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "annotation_handle",
                "annotation_text",
                "final_status",
                "shape_code",
                "shape_name",
                "production_length_cm",
                "declared_length_cm",
                "production_length_source",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "annotation_handle": "T1",
                "annotation_text": "2N10x100",
                "final_status": "APPROVED",
                "shape_code": "F001",
                "shape_name": "ПРАВ ПРЪТ",
                "production_length_cm": "100",
            }
        )
    _write_json(
        working / "approved_geometry_links.json",
        {
            "links": [
                {
                    "annotation_handle": "T1",
                    "annotation_text": "7N12x100",
                    "resolved_annotation_text": "7N12x100",
                    "production_count": 7,
                    "production_diameter_mm": 12,
                    "production_length_cm": 100,
                    "production_segment_lengths_cm": [100],
                    "geometry_handles": ["G1"],
                    "master_shape_code": "F001",
                    "master_shape_name": "ПРАВ ПРЪТ",
                    "approval_status": "ENGINEER_RESOLVED",
                }
            ]
        },
    )
    project = ProjectModel(name="P", root=str(tmp_path))
    entities = [
        DxfEntityRecord("C", "TEXT", "$Ramka", {"insert": [0, 0, 0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord("TITLE", "TEXT", "Layer1", {"insert": [100, 0, 0]}, "ДОЛНА АРМИРОВКА"),
        DxfEntityRecord("T1", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [-100, 500, 0]}, "2N10x100"),
        DxfEntityRecord("G1", "LINE", "Arm - N10", {"start": [-100, 500, 0], "end": [0, 500, 0]}),
    ]
    engine = EnginePackage(
        engine_id="E",
        title="E",
        version="1",
        shapes=[
            ShapeDefinition(
                code="F001",
                name="ПРАВ ПРЪТ",
                active=True,
                segments=[SegmentDefinition(index=1, length_code="L1")],
            )
        ],
    )
    _frames, positions = load_export_context(project, entities, engine)
    assert len(positions) == 1
    assert positions[0].annotation_text == "7N12x100"
    assert positions[0].count == 7
    assert positions[0].diameter_mm == 12.0


def test_learning_pack_separates_confirmations_from_rule_candidates(tmp_path: Path) -> None:
    base_event = {
        "session_id": "S1",
        "severity": "INFO",
        "project_name": "P",
        "project_root": "",
        "source_sha256": "dxf",
        "engine_version": "0.3.13",
        "matcher_sha256": "matcher",
        "report_path": None,
    }
    events = [
        {
            **base_event,
            "event_id": 1,
            "at": "2026-08-13T08:00:00+03:00",
            "event_type": "APPROVED",
            "annotation_handle": "A1",
            "annotation_text": "2N12x700",
            "payload": {
                "annotation_handle": "A1",
                "annotation_text": "2N12x700",
                "row_context": {"master_shape_code": "F003", "matcher_disposition": "CONTROL"},
            },
        },
        {
            **base_event,
            "event_id": 2,
            "at": "2026-08-13T08:01:00+03:00",
            "event_type": "ENGINEER_RESOLVED",
            "annotation_handle": "A2",
            "annotation_text": "29N8x94",
            "payload": {
                "annotation_handle": "A2",
                "annotation_text": "29N8x94",
                "resolved_annotation_text": "29N8x94",
                "action_class": "CORRECTION",
                "reason_code": "WRONG_SHAPE",
                "note": "Правилната форма е F005.",
                "original": {"shape_code": "F003", "review_reason_codes": ["OWNERSHIP_CONFLICT"]},
                "resolved": {
                    "shape_code": "F005",
                    "shape_name": "ФИБА",
                    "count": 29,
                    "diameter_mm": 8,
                    "production_length_cm": 94,
                    "segment_lengths_cm": [40, 14, 40],
                },
                "geometry": {"handles": ["G2"]},
            },
        },
    ]
    result = build_learning_pack(
        session_id="S1",
        started_at="2026-08-13T08:00:00+03:00",
        ended_at="2026-08-13T18:00:00+03:00",
        events=events,
        projects=[],
        output_dir=tmp_path,
    )
    with zipfile.ZipFile(result.path) as archive:
        calibration = json.loads(archive.read("session/calibration_evidence.json"))
        candidates = json.loads(archive.read("session/rule_candidates.json"))
    assert len(calibration["confirmations"]) == 1
    assert calibration["confirmations"][0]["accepted_shape_code"] == "F003"
    assert calibration["confirmations"][0]["examples"][0]["annotation_handle"] == "A1"
    assert len(candidates["candidates"]) == 1
    candidate = candidates["candidates"][0]
    assert candidate["candidate_type"] == "SHAPE_CORRECTION"
    assert candidate["maturity"] == "OBSERVATION"
    assert candidate["chosen_shape_code"] == "F005"


def test_export_includes_explicit_engineer_position_without_dxf_geometry(
    tmp_path: Path,
) -> None:
    working = tmp_path / "working"
    working.mkdir()
    _write_json(
        working / "final_integrity_report.json",
        {
            "integrity_passed": True,
            "full_specification_complete": True,
            "duplicate_groups_remaining": 0,
        },
    )
    with (working / "final_integrity_ledger.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "annotation_handle",
                "original_annotation_text",
                "resolved_annotation_text",
                "final_status",
                "shape_code",
                "shape_name",
                "production_length_cm",
                "declared_length_cm",
                "production_length_source",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "annotation_handle": "T1",
                "original_annotation_text": "60N8/s=20",
                "resolved_annotation_text": "60N8x94",
                "final_status": "APPROVED",
                "shape_code": "F005",
                "shape_name": "ФИБА",
                "production_length_cm": "94",
                "production_length_source": "OWNER_ENGINEER_DECISION",
            }
        )
    _write_json(
        working / "approved_geometry_links.json",
        {
            "links": [
                {
                    "annotation_handle": "T1",
                    "source_annotation_text": "60N8/s=20",
                    "resolved_annotation_text": "60N8x94",
                    "production_count": 60,
                    "production_diameter_mm": 8,
                    "production_length_cm": 94,
                    "production_segment_lengths_cm": [40, 14, 40],
                    "geometry_handles": [],
                    "geometry_optional_by_engineer": True,
                    "master_shape_code": "F005",
                    "master_shape_name": "ФИБА",
                    "approval_status": "ENGINEER_RESOLVED",
                    "approved_by": "OWNER_ENGINEER_DECISION",
                }
            ]
        },
    )
    project = ProjectModel(name="P", root=str(tmp_path))
    entities = [
        DxfEntityRecord("C", "TEXT", "$Ramka", {"insert": [0, 0, 0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord("TITLE", "TEXT", "Layer1", {"insert": [100, 0, 0]}, "ДОЛНА АРМИРОВКА"),
        DxfEntityRecord("T1", "TEXT", "ARM_TXT", {"insert": [-100, 500, 0]}, "60N8/s=20"),
    ]
    engine = EnginePackage(
        engine_id="E",
        title="E",
        version="1",
        shapes=[
            ShapeDefinition(
                code="F005",
                name="ФИБА",
                active=True,
                segments=[
                    SegmentDefinition(index=1, length_code="L1", bend_after_deg=90),
                    SegmentDefinition(index=2, length_code="L2", bend_after_deg=90),
                    SegmentDefinition(index=3, length_code="L1"),
                ],
            )
        ],
    )
    _frames, positions = load_export_context(project, entities, engine)
    assert len(positions) == 1
    assert positions[0].annotation_text == "60N8x94"
    assert positions[0].count == 60
    assert positions[0].diameter_mm == 8.0
    assert positions[0].geometry_handles == ()
    assert positions[0].segment_lengths_cm == (40.0, 14.0, 40.0)
