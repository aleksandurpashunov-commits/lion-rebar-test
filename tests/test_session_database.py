from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from lion_rebar.session import SessionService
from lion_rebar.utils.app_paths import AppPaths


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


def test_session_database_survives_service_restart(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "appdata")
    service = SessionService(paths, app_version="test")
    session_id = service.session_id
    project_root = tmp_path / "Project_A"
    (project_root / "working").mkdir(parents=True)
    project = SimpleNamespace(
        name="Project A",
        root=str(project_root),
        engine_id="lion-rebar-master",
        engine_version="0.3.13",
        engine_sha256="engine-sha",
        extensions={"source_sha256": "dxf-sha"},
    )
    service.record_project_event(
        project,
        "APPROVED",
        {"annotation_handle": "A1", "annotation_text": "2N12x700"},
    )
    summary = service.summary()
    assert summary.session_id == session_id
    assert summary.project_count == 1
    assert summary.decision_count == 1

    resumed = SessionService(paths, app_version="test")
    assert resumed.session_id == session_id
    assert resumed.summary().decision_count == 1
    events = resumed.database.list_events(session_id)
    assert any(event["event_type"] == "APPROVED" for event in events)
    assert paths.session_recovery_file.is_file()


def test_end_session_closes_current_and_next_action_creates_new(tmp_path: Path, monkeypatch) -> None:
    paths = _paths(tmp_path / "appdata")
    service = SessionService(paths, app_version="test")
    first = service.session_id

    # Avoid project diagnostics in this isolated unit test.
    monkeypatch.setattr(
        "lion_rebar.session.learning_pack.create_diagnostic_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("not expected")),
    )
    result = service.end_current_session(output_dir=tmp_path / "packs")
    assert result.path.is_file()
    assert result.latest_path.is_file()
    assert service.active_summary() is None
    recovery = json.loads(paths.session_recovery_file.read_text(encoding="utf-8"))
    assert recovery["status"] == "ENDED"
    assert recovery["learning_pack_latest"] == str(result.latest_path)

    service.record_system_event("NEXT_DAY_ACTION", {})
    second = service.session_id
    assert second != first
    state = json.loads(paths.session_state_file.read_text(encoding="utf-8"))
    assert state["session_id"] == second


def test_end_session_succeeds_when_cloud_publication_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path / "appdata")
    service = SessionService(paths, app_version="test")
    session_id = service.session_id

    monkeypatch.setattr(
        "lion_rebar.session.learning_pack.create_diagnostic_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("not expected")),
    )
    invalid_cloud = tmp_path / "cloud-target-is-a-file"
    invalid_cloud.write_text("not a directory", encoding="utf-8")

    result = service.end_current_session(output_dir=invalid_cloud)

    assert result.path.parent == paths.learning_packs
    assert result.path.is_file()
    assert result.latest_path.is_file()
    assert result.publication_warning
    assert result.published_path is None
    assert service.active_summary() is None

    recovery = json.loads(paths.session_recovery_file.read_text(encoding="utf-8"))
    assert recovery["status"] == "ENDED"
    assert recovery["summary"]["session_id"] == session_id
    assert recovery["learning_pack"] == str(result.path)
    assert recovery["learning_pack_publication_warning"]


def test_bulk_approval_counts_each_engineering_decision(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "appdata")
    service = SessionService(paths, app_version="test")
    project_root = tmp_path / "Project_B"
    (project_root / "working").mkdir(parents=True)
    project = SimpleNamespace(
        name="Project B",
        root=str(project_root),
        engine_id="lion-rebar-master",
        engine_version="0.3.13",
        engine_sha256="engine-sha",
        extensions={"source_sha256": "dxf-sha"},
    )
    service.record_project_event(
        project,
        "BULK_APPROVED",
        {
            "mode": "GEOMETRY_SAMPLE",
            "annotation_handles": ["A1", "A2", "A3"],
            "decisions": [
                {"annotation_handle": "A1", "annotation_text": "2N12x700"},
                {"annotation_handle": "A2", "annotation_text": "3N16x500"},
                {"annotation_handle": "A3", "annotation_text": "4N8x160"},
            ],
        },
    )
    assert service.summary().decision_count == 3


def test_end_session_falls_back_to_emergency_local_storage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from lion_rebar.session import service as service_module
    from lion_rebar.session.learning_pack import LearningPackStorageError

    paths = _paths(tmp_path / "appdata")
    service = SessionService(paths, app_version="test")
    session_id = service.session_id
    real_build = service_module.build_learning_pack
    calls: list[Path] = []

    def fail_primary_then_build(**kwargs):
        output_dir = Path(kwargs["output_dir"])
        calls.append(output_dir)
        if output_dir == paths.learning_packs:
            raise LearningPackStorageError("File is not a zip file")
        return real_build(**kwargs)

    monkeypatch.setattr(service_module, "build_learning_pack", fail_primary_then_build)
    monkeypatch.setattr(
        service_module.tempfile,
        "gettempdir",
        lambda: str(tmp_path / "system-temp"),
    )

    result = service.end_current_session()

    assert calls[0] == paths.learning_packs
    assert len(calls) == 2
    assert result.path.is_file()
    assert result.latest_path.is_file()
    assert result.local_storage_warning
    assert "File is not a zip file" in result.local_storage_warning
    assert service.active_summary() is None
    recovery = json.loads(paths.session_recovery_file.read_text(encoding="utf-8"))
    assert recovery["status"] == "ENDED"
    assert recovery["summary"]["session_id"] == session_id
    assert recovery["learning_pack_local_storage_warning"]


def test_end_session_treats_output_dir_only_as_secondary_publication(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "appdata")
    service = SessionService(paths, app_version="test")
    cloud = tmp_path / "cloud"

    result = service.end_current_session(output_dir=cloud)

    assert result.path.parent == paths.learning_packs
    assert result.published_path is not None
    assert result.published_path.parent == cloud
    assert service.active_summary() is None


def test_application_upgrade_resumes_the_same_active_session(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "appdata")
    before = SessionService(paths, app_version="0.1.44.15-recovery.6")
    session_id = before.session_id
    before.record_system_event("PRE_UPGRADE_DECISION", {"value": 1})

    after = SessionService(paths, app_version="0.1.44.16-recovery.6.1")

    assert after.session_id == session_id
    assert after.active_summary() is not None
    assert after.summary().event_count >= 2  # original event + SESSION_RESUMED


def test_session_summary_counts_engineer_problem_and_exclusion_events(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "appdata")
    service = SessionService(paths, app_version="test")
    project_root = tmp_path / "Project_C"
    (project_root / "working").mkdir(parents=True)
    project = SimpleNamespace(
        name="Project C",
        root=str(project_root),
        engine_id="lion-rebar-master",
        engine_version="0.3.13",
        engine_sha256="engine-sha",
        extensions={"source_sha256": "dxf-sha"},
    )
    service.record_project_event(project, "PROBLEM_RECORDED", {"annotation_handle": "A1"})
    service.record_project_event(project, "ENGINEER_RESOLVED", {"annotation_handle": "A1"})
    service.record_project_event(project, "EXCLUDED_BY_ENGINEER", {"annotation_handle": "A2"})

    summary = service.summary()
    assert summary.decision_count == 3
    events = service.database.list_events(service.session_id)
    counts = {name: sum(row["event_type"] == name for row in events) for name in (
        "PROBLEM_RECORDED",
        "ENGINEER_RESOLVED",
        "EXCLUDED_BY_ENGINEER",
    )}
    assert counts == {
        "PROBLEM_RECORDED": 1,
        "ENGINEER_RESOLVED": 1,
        "EXCLUDED_BY_ENGINEER": 1,
    }
