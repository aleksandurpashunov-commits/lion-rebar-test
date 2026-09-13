from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.project.data_identity import begin_analysis_run, current_identity, stamp_payload
from lion_rebar.project.integrity import reconcile_project_integrity
from lion_rebar.project.manager import ProjectManager
from lion_rebar.project.non_production import is_valid_non_production_decision
from lion_rebar.review_center_model import is_review_row_resolved, review_queue_rows


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _valid_non_production_decision() -> dict:
    return {
        "decision": "NON_PRODUCTION_POSITION",
        "action_class": "EXCLUSION",
        "reason_code": "NON_PRODUCTION_POSITION",
        "note": "Проверено в оригиналния DXF: това е дублиран надпис.",
        "evidence": {
            "annotation_handle": "T1",
            "original_checked": True,
            "diagnostic_code": "DUPLICATE_ANNOTATION",
        },
    }


def _invalid_non_production_decision() -> dict:
    value = _valid_non_production_decision()
    value["evidence"] = {}
    return value


@pytest.mark.parametrize(
    "mutation",
    [
        {"decision": "ENGINEER_RESOLVED"},
        {"action_class": "PROBLEM"},
        {"reason_code": "DUPLICATE_ANNOTATION"},
        {"note": "   "},
        {"evidence": {}},
        {"evidence": None},
    ],
)
def test_shared_non_production_validator_rejects_incomplete_contract(
    mutation: dict,
) -> None:
    invalid = {**_valid_non_production_decision(), **mutation}
    assert is_valid_non_production_decision(invalid) is False


def test_shared_non_production_validator_accepts_complete_contract() -> None:
    assert is_valid_non_production_decision(_valid_non_production_decision()) is True


def test_invalid_non_production_decision_stays_in_review_work_queue() -> None:
    row = {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": "T1",
        "annotation_text": "8N10x1200",
        "annotation_count": 8,
        "diameter_mm": 10,
        "declared_length_cm": 1200,
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "candidate_handles": [],
        "master_shape_code": "F001",
        "master_shape_name": "ПРАВ ПРЪТ",
    }
    invalid = _invalid_non_production_decision()

    assert not is_review_row_resolved(
        row,
        decision_code="NON_PRODUCTION_POSITION",
        decision_payload=invalid,
    )
    assert [
        item["annotation_handle"]
        for item in review_queue_rows(
            [row],
            queue_code="WORK",
            decisions={"T1": invalid},
        )
    ] == ["T1"]

    valid = _valid_non_production_decision()
    assert is_review_row_resolved(
        row,
        decision_code="NON_PRODUCTION_POSITION",
        decision_payload=valid,
    )
    assert review_queue_rows(
        [row],
        queue_code="WORK",
        decisions={"T1": valid},
    ) == []


def _engine() -> EnginePackage:
    return EnginePackage(
        engine_id="RC36-NON-PRODUCTION-TEST",
        title="RC36 test engine",
        version="0.3.13",
        shapes=[
            ShapeDefinition(
                code="F001",
                name="ПРАВ ПРЪТ",
                state="LOCKED",
                locked=True,
                active=True,
                segments=[SegmentDefinition(index=1, length_code="L1")],
            )
        ],
    )


def _project_with_invalid_exclusion(tmp_path: Path) -> tuple[Path, Path]:
    manager = ProjectManager()
    project = manager.create_project(tmp_path, "RC36", _engine())
    manager.save_engine_snapshot(project, _engine())

    input_dxf = tmp_path / "input.dxf"
    input_dxf.write_bytes(b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")
    manager.import_dxf_source(project, input_dxf)
    identity = begin_analysis_run(project)
    manager.save(project)

    annotation = DxfEntityRecord(
        "T1",
        "BLOCK_ATTR_TEXT",
        "ARM_TXT",
        {"insert": [100.0, 500.0, 0.0], "visual_anchor": [100.0, 500.0, 0.0]},
        "8N10x1200",
    )
    manager.save_extracted_entities(
        project,
        {
            "schema": "lion-rebar-extracted-entities/v1",
            "entities": [annotation.to_dict()],
        },
    )

    report_row = {
        "annotation_handle": "T1",
        "annotation_text": "8N10x1200",
        "annotation_class": "ACTIONABLE",
        "annotation_count": 8,
        "diameter_mm": 10.0,
        "declared_length_cm": 1200.0,
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "matcher_disposition": "REVIEW",
        "master_shape_code": "F001",
        "master_shape_name": "ПРАВ ПРЪТ",
        "candidate_handles": [],
    }
    stamp_payload(report_row, identity)
    report_payload = {
        "schema": "lion-rebar-geometry-batch-report/v3",
        "project_name": project.name,
        "rows": [report_row],
    }
    stamp_payload(report_payload, identity)
    report_path = Path(project.root) / "output" / "04_REVIEW_REPORTS" / "current.json"
    _write_json(report_path, report_payload)

    state_payload = {
        "schema": "lion-rebar-review-center-state/v2",
        "decisions": {"T1": _invalid_non_production_decision()},
    }
    stamp_payload(state_payload, current_identity(project))
    _write_json(Path(project.root) / "working" / "review_center_state.json", state_payload)

    approved_payload = {
        "schema": "lion-rebar-approved-geometry-links/v1",
        "project_name": project.name,
        "links": [],
        "history": [],
    }
    stamp_payload(approved_payload, current_identity(project))
    _write_json(
        Path(project.root) / "working" / "approved_geometry_links.json",
        approved_payload,
    )
    return Path(project.root), report_path


def test_invalid_non_production_evidence_is_engineering_pending_and_non_terminal(
    tmp_path: Path,
) -> None:
    project_root, report_path = _project_with_invalid_exclusion(tmp_path)

    result = reconcile_project_integrity(
        project_root,
        report_path=report_path,
        apply=True,
    )

    assert result.full_specification_complete is False
    assert result.non_production_positions == 0
    assert result.unresolved_positions == 1
    assert result.safe_confirmation_pending == 0
    assert result.engineering_decisions_pending == 1
    assert {
        issue.code for issue in result.issues
    } >= {"NON_PRODUCTION_EVIDENCE_INVALID", "ENGINEERING_DECISIONS_PENDING"}

    ledger_path = Path(result.ledger_path or "")
    assert ledger_path.is_file()
    with ledger_path.open("r", encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["annotation_handle"] == "T1"
    assert row["final_status"] == "PROBLEM_UNRESOLVED"
    assert row["final_status"] != "NON_PRODUCTION_POSITION"


def test_review_center_round_trip_preserves_authoritative_exclusion_evidence(
    tmp_path: Path,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from lion_rebar.ui.review_center import ReviewCenterDialog

    app = QApplication.instance() or QApplication([])
    report_path = tmp_path / "report.json"
    state_path = tmp_path / "review_center_state.json"
    _write_json(
        report_path,
        {
            "schema": "lion-rebar-geometry-batch-report/v3",
            "project_name": "RC36",
            "generated_at": "2026-09-04T00:00:00+03:00",
            "summary": {},
            "rows": [
                {
                    "annotation_class": "ACTIONABLE",
                    "annotation_handle": "T1",
                    "annotation_text": "8N10x1200",
                    "annotation_count": 8,
                    "diameter_mm": 10,
                    "declared_length_cm": 1200,
                    "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
                    "matcher_disposition": "REVIEW",
                    "candidate_handles": [],
                    "master_shape_code": "F001",
                    "master_shape_name": "ПРАВ ПРЪТ",
                    "data_quality_flags": [],
                    "data_quality_notes": [],
                    "candidate_notes": [],
                    "analysis_warnings": [],
                }
            ],
        },
    )
    dialog = ReviewCenterDialog(report_path, state_path=state_path)

    authoritative = _valid_non_production_decision()
    disk_state = dict(dialog._state)
    disk_state["decisions"] = {"T1": authoritative}
    _write_json(state_path, disk_state)
    dialog._state.setdefault("decisions", {})["T1"] = {
        "decision": "PROBLEM_RECORDED",
    }

    returned = dialog.mark_engineer_resolved(
        "T1",
        {
            "annotation": {"handle": "T1", "resolved_text": "8N10x1200"},
            "resolved": {},
            "reason_code": "NON_PRODUCTION_POSITION",
            "action_class": "EXCLUSION",
            "note": authoritative["note"],
            # This deliberately mirrors the rc35 UI payload which did not carry
            # the structured evidence already persisted by the project layer.
            "evidence": {},
            "diagnostic_problem": {},
        },
    )
    app.processEvents()

    persisted = json.loads(state_path.read_text(encoding="utf-8-sig"))
    assert returned == authoritative
    assert persisted["decisions"]["T1"] == authoritative
    assert is_valid_non_production_decision(persisted["decisions"]["T1"])
    assert dialog._is_resolved(dict(dialog.report.rows[0]))
    dialog.close()

