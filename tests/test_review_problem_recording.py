from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from lion_rebar.ui.review_center import ReviewCenterDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write_report(path: Path) -> None:
    row = {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": "ANN-N8",
        "annotation_text": "2N8x1050",
        "annotation_count": 2,
        "diameter_mm": 8,
        "declared_length_cm": 1050,
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "candidate_handles": ["N25-GEOM"],
        "alternative_candidates": [{"handles": ["OTHER-WRONG"]}],
        "master_shape_code": "F004",
        "master_shape_name": "ПРЪТ С ЕДИН ВИНКЕЛ",
        "data_quality_flags": [],
        "data_quality_notes": [],
        "candidate_notes": [],
        "analysis_warnings": [],
    }
    payload = {
        "schema": "lion-rebar-geometry-batch-report/v3",
        "project_name": "Test",
        "generated_at": "2026-07-26T00:00:00+03:00",
        "summary": {},
        "rows": [row],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_no_valid_candidate_is_persisted_across_report_fingerprints(tmp_path: Path):
    _app()
    report1 = tmp_path / "report1.json"
    state = tmp_path / "review_center_state.json"
    _write_report(report1)
    dialog = ReviewCenterDialog(report1, state_path=state)
    row = dict(dialog.report.rows[0])
    decision = {
        "decision": "NO_VALID_CANDIDATE",
        "reason_code": "WRONG_OBJECT",
        "reason": "Маркира чужда позиция / друг диаметър",
        "note": "N8 маркира N25",
        "rejected_candidate_handles": ["N25-GEOM", "OTHER-WRONG"],
    }
    dialog._state.setdefault("decisions", {})["ANN-N8"] = decision
    dialog._save_state()
    dialog._persist_problem("ANN-N8", decision)
    dialog.close()

    report2 = tmp_path / "report2.json"
    _write_report(report2)
    payload = json.loads(report2.read_text(encoding="utf-8"))
    payload["generated_at"] = "2026-07-26T01:00:00+03:00"
    report2.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    dialog2 = ReviewCenterDialog(report2, state_path=state)

    assert dialog2._decision("ANN-N8") == "NO_VALID_CANDIDATE"
    assert dialog2._state["decisions"]["ANN-N8"]["note"] == "N8 маркира N25"
    dialog2.close()
