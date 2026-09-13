from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.review.apply_to_group import (
    GroupApplicationPlan,
    GroupMember,
    apply_group_plan,
    undo_group_operation,
)
from lion_rebar.session.decision_contract import (
    append_project_decision_event,
    flush_project_decision_outbox,
    pending_project_decision_events,
)
from lion_rebar.session.learning_pack import (
    _build_rule_candidates,
    _current_learning_rows,
    _decision_rows,
    build_learning_pack,
)
from lion_rebar.session.service import SessionService
from lion_rebar.utils.app_paths import AppPaths


def _event(project: str, source_sha: str, handle: str, shape: str, *, event_id: int = 1, dimensions=None):
    return {
        "event_id": event_id,
        "at": f"2026-09-05T12:00:0{event_id}+03:00",
        "event_type": "ENGINEER_RESOLVED",
        "project": project,
        "source_sha256": source_sha,
        "engine_version": "0.3.14",
        "annotation_handle": handle,
        "annotation_text": "1N10x47",
        "payload": {
            "annotation_handle": handle,
            "action_class": "CORRECTION",
            "reason_code": "WRONG_SHAPE",
            "original": {"shape_code": "F001"},
            "resolved": {
                "shape_code": shape,
                "shape_revision": 1,
                "count": 1,
                "diameter_mm": 10,
                "production_length_cm": 47,
            },
            "dimensions": dimensions,
        },
    }


def _paths(root: Path) -> AppPaths:
    return AppPaths(
        root=root,
        engine_registry=root / "engine_registry",
        app_logs=root / "logs",
        settings_file=root / "settings.json",
        session_root=root / "session_core",
        session_db=root / "session_core" / "sessions.sqlite3",
        session_state_file=root / "session_core" / "current_session.json",
        session_recovery_file=root / "session_core" / "SESSION_RECOVERY_LATEST.json",
        learning_packs=root / "session_core" / "learning_packs",
    )


def _project(root: Path):
    working = root / "working"
    working.mkdir(parents=True)
    project_payload = {
        "name": root.name,
        "engine_id": "lion-rebar-master",
        "engine_version": "0.3.14",
        "engine_sha256": "engine",
        "extensions": {
            "source_sha256": "same-dxf",
            "data_identity": {
                "project_id": "P1",
                "source_sha256": "same-dxf",
                "source_generation_id": "G1",
                "analysis_run_id": "R1",
            },
        },
    }
    (working / "project.json").write_text(json.dumps(project_payload), encoding="utf-8")
    return SimpleNamespace(
        name=root.name,
        root=str(root),
        engine_id="lion-rebar-master",
        engine_version="0.3.14",
        engine_sha256="engine",
        extensions=project_payload["extensions"],
    )


def test_old_dimensions_mapping_never_turns_field_names_into_segments() -> None:
    rows = _decision_rows([
        _event("P1", "dxf", "A1", "F007", dimensions={"declared_length_cm": 47}),
        _event(
            "P1", "dxf", "A2", "F008", event_id=2,
            dimensions={"production_segment_lengths_cm": [10, "13", 21]},
        ),
    ])
    assert rows[0]["accepted_segment_lengths_cm"] == []
    assert rows[0]["field_completeness"]["production_segments_cm"] is False
    assert rows[1]["accepted_segment_lengths_cm"] == [10.0, 13.0, 21.0]
    assert all(isinstance(value, float) and value > 0 for value in rows[1]["accepted_segment_lengths_cm"])
    assert "declared_length_cm" not in rows[0]["accepted_segment_lengths_cm"]


def test_straight_bar_may_derive_its_only_segment_from_explicit_total() -> None:
    row = _decision_rows([_event("P1", "dxf", "A1", "F001", dimensions={})])[0]
    assert row["accepted_segment_lengths_cm"] == [47.0]
    assert row["field_provenance"]["accepted_segments"] == "DERIVED_FROM_EXPLICIT_ACCEPTED_TOTAL_FOR_F001"


def test_group_apply_and_undo_create_one_operation_with_position_consequences(tmp_path: Path) -> None:
    root = tmp_path / "P"
    _project(root)
    identity = DataIdentity("P1", "same-dxf", "G1", "R1")
    plan = GroupApplicationPlan(
        representative_handle="REP",
        group_key="g",
        signature={},
        members=[
            GroupMember("A1", "1N10x47", True),
            GroupMember("A2", "1N10x47", True),
        ],
        changes={"master_shape_code": "F008", "production_segments_cm": [10, 13, 21]},
    )
    rows = {
        handle: {
            "annotation_handle": handle,
            "annotation_text": "1N10x47",
            "annotation_count": 1,
            "diameter_mm": 10,
            "declared_length_cm": 47,
            "master_shape_code": "F001",
            "candidate_handles": [f"G-{handle}"],
        }
        for handle in ("A1", "A2")
    }
    operation_id = apply_group_plan(
        root, plan, rows, identity, applied_by="Engineer", note="same proven detail",
        save_decision=lambda *_args, **_kwargs: None,
    )
    pending = pending_project_decision_events(root)
    assert len(pending) == 1
    assert pending[0]["operation_id"] == operation_id
    assert len(pending[0]["payload"]["consequences"]) == 2
    restored = undo_group_operation(root, operation_id, undone_by="Engineer", note="wrong group")
    assert restored == 2
    pending = pending_project_decision_events(root)
    assert {row["event_type"] for row in pending} == {"GROUP_APPLIED", "GROUP_UNDONE"}


def test_repeat_import_and_renamed_copy_do_not_create_independent_evidence() -> None:
    left = _event("Original", "same-dxf", "A1", "F008", dimensions=[10, 13, 21])
    right = _event("Renamed copy", "same-dxf", "A1", "F008", event_id=2, dimensions=[10, 13, 21])
    rows = _decision_rows([left, left, right])
    current = _current_learning_rows(rows)
    candidates = _build_rule_candidates(rows)
    assert len(current) == 1
    assert len(candidates) == 1
    assert candidates[0]["repeat_count"] == 1
    assert candidates[0]["independent_drawing_family_count"] == 1
    assert candidates[0]["maturity"] == "OBSERVATION"


def test_conflicting_labels_are_preserved_and_not_resolved_by_last_file() -> None:
    rows = _decision_rows([
        _event("P1", "same-dxf", "A1", "F007", dimensions=[10, 13, 21]),
        _event("P1 copy", "same-dxf", "A1", "F008", event_id=2, dimensions=[10, 13, 21]),
    ])
    current = _current_learning_rows(rows)
    assert {row["accepted_shape_code"] for row in current} == {"F007", "F008"}
    assert all(row["decision_conflict"] for row in current)
    assert all(row["maturity"] == "CONFLICT_REQUIRES_REVIEW" for row in _build_rule_candidates(rows))


def test_outbox_recovers_after_delivery_failure_without_double_count(tmp_path: Path) -> None:
    project = _project(tmp_path / "P")
    envelope = append_project_decision_event(
        project.root,
        "ENGINEER_RESOLVED",
        {"annotation_handle": "A1", "resolved": {"shape_code": "F008"}},
    )
    with pytest.raises(RuntimeError):
        flush_project_decision_outbox(project.root, lambda _event: (_ for _ in ()).throw(RuntimeError("offline")))
    assert len(pending_project_decision_events(project.root)) == 1

    service = SessionService(_paths(tmp_path / "appdata"), app_version="rc39-test")

    def deliver(event):
        payload = dict(event["payload"])
        payload["human_decision"] = dict(event)
        payload["stable_event_id"] = event["event_id"]
        service.record_project_event(project, event["event_type"], payload, occurred_at=event["occurred_at"])
        return service.session_id

    assert flush_project_decision_outbox(project.root, deliver) == (1, 0)
    service.record_project_event(
        project,
        "ENGINEER_RESOLVED",
        {"annotation_handle": "A1", "stable_event_id": envelope["event_id"]},
    )
    events = [row for row in service.database.list_events(service.session_id) if row["event_type"] == "ENGINEER_RESOLVED"]
    assert len(events) == 1


def test_pack_reports_candidates_but_activates_no_rule(tmp_path: Path) -> None:
    event = _event("P1", "dxf", "A1", "F008", dimensions=[10, 13, 21])
    result = build_learning_pack(
        session_id="S-RC39",
        started_at=event["at"],
        ended_at=event["at"],
        events=[event],
        projects=[],
        output_dir=tmp_path,
    )
    with zipfile.ZipFile(result.path) as archive:
        summary = json.loads(archive.read("session/summary.json"))
        metrics = json.loads(archive.read("session/decision_metrics.json"))
    assert summary["candidate_count"] == 1
    assert summary["activated_rule_count"] == 0
    assert metrics["activated_rule_count"] == 0
