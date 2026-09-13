from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QHeaderView

import lion_rebar.ui.review_center as review_center_module
import lion_rebar.ui.main_window as main_window_module
from lion_rebar.ui.main_window import MainWindow
from lion_rebar.ui.review_center import QUEUE_DEFINITIONS, ReviewCenterDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write_large_report(path: Path, *, rows: int = 807) -> None:
    payload_rows = []
    for index in range(rows):
        payload_rows.append(
            {
                "annotation_class": "ACTIONABLE",
                "annotation_handle": f"ANN-{index:04d}",
                "annotation_text": f"{index + 1}N10x{400 + index}",
                "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
                "matcher_disposition": "REVIEW",
                "candidate_handles": [f"G-{index:04d}"],
                "alternative_candidates": [],
                "master_shape_code": "F019" if index == rows - 1 else "F001",
                "master_shape_name": "TEST SHAPE",
                "diameter_mm": 10,
                "declared_length_cm": 400 + index,
                "segment_count": 1,
                "data_quality_flags": [],
                "data_quality_notes": [],
                "candidate_notes": [],
                "analysis_warnings": [],
            }
        )
    path.write_text(
        json.dumps(
            {
                "schema": "lion-rebar-geometry-batch-report/v3",
                "project_name": "PERFORMANCE",
                "generated_at": "2026-07-28T15:00:00+03:00",
                "summary": {},
                "rows": payload_rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_large_review_uses_one_cached_build_per_queue_and_debounced_search(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _app()
    report = tmp_path / "large_report.json"
    state = tmp_path / "review_center_state.json"
    _write_large_report(report)

    original = review_center_module.review_queue_rows
    calls: list[str] = []

    def counted(*args, **kwargs):
        calls.append(str(kwargs.get("queue_code") or ""))
        return original(*args, **kwargs)

    monkeypatch.setattr(review_center_module, "review_queue_rows", counted)
    dialog = ReviewCenterDialog(report, state_path=state)

    # "MISSING_MASTER" no longer has its own dropdown entry (Фаза 2 queue
    # consolidation folded it into "PROBLEMS"), but the summary label still
    # queries it directly by code (review_center.py, missing_master count) —
    # it must still be cached exactly once, same as every dropdown code.
    expected_codes = {code for code, _label in QUEUE_DEFINITIONS} | {"MISSING_MASTER"}
    assert Counter(calls) == Counter({code: 1 for code in expected_codes})
    assert (
        dialog.table.horizontalHeader().sectionResizeMode(0)
        != QHeaderView.ResizeMode.ResizeToContents
    )

    calls_before_selection = len(calls)
    dialog._selection_changed()
    assert len(calls) == calls_before_selection

    dialog.search_edit.setText("ANN-0806")
    assert dialog._search_timer.isActive()
    dialog._search_timer.stop()
    dialog._apply_search_filter()
    assert dialog.table.rowCount() == 1
    assert dialog.current_row()["master_shape_code"] == "F019"
    assert len(calls) == calls_before_selection

    dialog.search_edit.setText("")
    dialog._search_timer.stop()
    dialog._apply_search_filter()
    dialog.mark_approved("ANN-0000")

    # One decision invalidates the derived state once. Each queue code may be
    # rebuilt once, but summary, buttons and selection must reuse it.
    assert Counter(calls) == Counter({code: 2 for code in expected_codes})
    dialog.close()


def test_close_time_diagnostic_is_scheduled_outside_gui_thread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    started = threading.Event()
    release_worker = threading.Event()
    completed = threading.Event()

    def fake_create(project_root, *, output_path, include_source_dxf, latest_report_path):
        assert Path(project_root) == tmp_path
        assert include_source_dxf is True
        started.set()
        release_worker.wait(timeout=2)
        completed.set()
        return Path(output_path)

    monkeypatch.setattr(main_window_module, "create_diagnostic_bundle", fake_create)
    fake_window = SimpleNamespace(
        project=SimpleNamespace(root=tmp_path, name="PERFORMANCE"),
        _diagnostic_thread=None,
        _diagnostic_lock=threading.Lock(),
        _find_latest_review_report=lambda: tmp_path / "report.json",
        logger=logging.getLogger("test.review.performance"),
    )

    before = time.perf_counter()
    MainWindow._schedule_ai_diagnostic_bundle(fake_window)
    scheduling_ms = (time.perf_counter() - before) * 1000.0

    assert scheduling_ms < 100.0
    assert started.wait(timeout=1)
    assert fake_window._diagnostic_thread.is_alive()
    release_worker.set()
    fake_window._diagnostic_thread.join(timeout=2)
    assert completed.is_set()
