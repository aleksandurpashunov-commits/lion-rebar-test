from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import (
    EnginePackage,
    SegmentDefinition,
    ShapeDefinition,
)
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engineer_decision import save_engineer_decision
from lion_rebar.export import auto_approval
from lion_rebar.export.auto_approval import auto_approve_gate_positions
from lion_rebar.export.engine_shape_parameters import active_production_shapes
from lion_rebar.export.errors import ExcelExportError
from lion_rebar.export.excel_export import load_export_context
from lion_rebar.project.data_identity import (
    IDENTITY_KEY,
    DataIdentity,
    begin_analysis_run,
    current_identity,
    stamp_payload,
)
from lion_rebar.project.integrity import reconcile_project_integrity
from lion_rebar.project.manager import ProjectManager


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _engine() -> EnginePackage:
    return EnginePackage(
        engine_id="TEST-ENGINE",
        title="TEST",
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


def _f003_engine() -> EnginePackage:
    return EnginePackage(
        engine_id="TEST-ENGINE",
        title="TEST",
        version="0.3.13",
        shapes=[
            ShapeDefinition(
                code="F003",
                name="ПРЪТ С ДВА ВИНКЕЛА",
                state="LOCKED",
                locked=True,
                active=True,
                segments=[
                    SegmentDefinition(index=1, length_code="A", bend_after_deg=90.0),
                    SegmentDefinition(index=2, length_code="B", bend_after_deg=90.0),
                    SegmentDefinition(index=3, length_code="C"),
                ],
            )
        ],
    )


def _beam_entities() -> list[DxfEntityRecord]:
    # One beam frame, one beam title and one reinforcement position.  The point
    # is deliberately placed in the deterministic ownership region below/right
    # of the title.
    return [
        DxfEntityRecord(
            "FRAME",
            "TEXT",
            "$Ramka",
            {"insert": [0.0, 1000.0, 0.0]},
            "ЧЕРТЕЖ:",
        ),
        DxfEntityRecord(
            "FRAME_TITLE",
            "TEXT",
            "TXT",
            {"insert": [100.0, 1000.0, 0.0]},
            "ГРЕДИ",
        ),
        DxfEntityRecord(
            "BEAM_TITLE",
            "TEXT",
            "TXT",
            {"insert": [0.0, 800.0, 0.0], "height": 10.0},
            "Гр А. 1",
        ),
        DxfEntityRecord(
            "T1",
            "BLOCK_ATTR_TEXT",
            "ARM_TXT",
            {"insert": [100.0, 666.0, 0.0], "visual_anchor": [100.0, 666.0, 0.0]},
            "2N10x100",
        ),
        DxfEntityRecord(
            "G1",
            "LINE",
            "Arm - N10",
            {"start": [50.0, 650.0, 0.0], "end": [150.0, 650.0, 0.0]},
        ),
    ]


def _bottom_entities(*, include_geometry: bool = True) -> list[DxfEntityRecord]:
    result = [
        DxfEntityRecord(
            "FRAME",
            "TEXT",
            "$Ramka",
            {"insert": [0.0, 0.0, 0.0]},
            "ЧЕРТЕЖ:",
        ),
        DxfEntityRecord(
            "FRAME_TITLE",
            "TEXT",
            "TXT",
            {"insert": [100.0, 0.0, 0.0]},
            "ДОЛНА АРМИРОВКА",
        ),
        DxfEntityRecord(
            "T1",
            "BLOCK_ATTR_TEXT",
            "ARM_TXT",
            {"insert": [-100.0, 500.0, 0.0], "visual_anchor": [-100.0, 500.0, 0.0]},
            "2N10x100",
        ),
    ]
    if include_geometry:
        result.append(
            DxfEntityRecord(
                "G1",
                "LINE",
                "Arm - N10",
                {"start": [-100.0, 500.0, 0.0], "end": [0.0, 500.0, 0.0]},
            )
        )
    return result


def _current_project(
    tmp_path: Path,
    *,
    beam: bool = False,
    geometryless: bool = False,
    with_link: bool = True,
    state_decision: dict | None = None,
    stale_report_run: bool = False,
    mixed_row_run: bool = False,
):
    manager = ProjectManager()
    project = manager.create_project(tmp_path, "P", _engine())
    manager.save_engine_snapshot(project, _engine())
    input_dxf = tmp_path / "input.dxf"
    input_dxf.write_bytes(b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")
    manager.import_dxf_source(project, input_dxf)
    identity = begin_analysis_run(project)
    manager.save(project)

    entities = _beam_entities() if beam else _bottom_entities(include_geometry=not geometryless)
    manager.save_extracted_entities(
        project,
        {"schema": "lion-rebar-extracted-entities/v1", "entities": [item.to_dict() for item in entities]},
    )

    report_identity = identity
    row_identity = identity
    if stale_report_run:
        report_identity = DataIdentity(
            project_id=identity.project_id,
            source_sha256=identity.source_sha256,
            source_generation_id=identity.source_generation_id,
            analysis_run_id="OLD-RUN",
        )
        row_identity = report_identity
    elif mixed_row_run:
        row_identity = DataIdentity(
            project_id=identity.project_id,
            source_sha256=identity.source_sha256,
            source_generation_id=identity.source_generation_id,
            analysis_run_id="OLD-ROW-RUN",
        )
    report_row = {
        "annotation_handle": "T1",
        "annotation_text": "2N10x100",
        "annotation_class": "ACTIONABLE",
        "annotation_count": 2,
        "diameter_mm": 10.0,
        "declared_length_cm": 100.0,
        "batch_status": "ОДОБРЕНА ВРЪЗКА" if with_link else "ЗА РЪЧЕН ПРЕГЛЕД",
        "master_shape_code": "F001",
        "master_shape_name": "ПРАВ ПРЪТ",
        "candidate_handles": [] if geometryless else ["G1"],
    }
    stamp_payload(report_row, row_identity)
    report_payload = {
        "schema": "lion-rebar-geometry-batch-report/v3",
        "rows": [report_row],
    }
    stamp_payload(report_payload, report_identity)
    for row in report_payload["rows"]:
        stamp_payload(row, row_identity)
    report_path = Path(project.root) / "output" / "04_REVIEW_REPORTS" / "current.json"
    _write_json(report_path, report_payload)

    state_payload = {
        "schema": "lion-rebar-review-center-state/v2",
        "decisions": {"T1": state_decision} if state_decision is not None else {},
    }
    stamp_payload(state_payload, identity)
    _write_json(Path(project.root) / "working" / "review_center_state.json", state_payload)

    links: list[dict] = []
    if with_link:
        link = {
            "annotation_handle": "T1",
            "annotation_text": "2N10x100",
            "resolved_annotation_text": "2N10x100",
            "annotation_count": 2,
            "production_count": 2,
            "diameter_mm": 10.0,
            "production_diameter_mm": 10.0,
            "declared_length_cm": 100.0,
            "production_length_cm": 100.0,
            "production_segment_lengths_cm": [100.0],
            "master_shape_code": "F001",
            "master_shape_name": "ПРАВ ПРЪТ",
            "geometry_handles": [] if geometryless else ["G1"],
            "geometry_handles_count": 0 if geometryless else 1,
            "geometry_provenance": (
                "NONE_ENGINEER_OVERRIDE" if geometryless else "DXF_HANDLES"
            ),
            "geometry_optional_by_engineer": bool(geometryless),
            "approval_status": "ENGINEER_RESOLVED — ПЪЛНА ПРОИЗВОДСТВЕНА СПЕЦИФИКАЦИЯ",
            "approved_by": "OWNER_ENGINEER_DECISION",
        }
        stamp_payload(link, identity)
        links.append(link)
    approved_payload = {
        "schema": "lion-rebar-approved-geometry-links/v1",
        "project_name": project.name,
        "links": links,
        "history": [],
    }
    stamp_payload(approved_payload, identity)
    _write_json(Path(project.root) / "working" / "approved_geometry_links.json", approved_payload)
    return manager, project, identity, entities, report_path


def test_new_source_generation_archives_all_derived_state(tmp_path: Path) -> None:
    manager = ProjectManager()
    project = manager.create_project(tmp_path, "ArchiveTest")
    source_a = tmp_path / "a.dxf"
    source_b = tmp_path / "b.dxf"
    source_a.write_bytes(b"A-DXF")
    source_b.write_bytes(b"B-DXF")

    manager.import_dxf_source(project, source_a)
    first = current_identity(project)
    working = Path(project.root) / "working"
    archives_before = set((working / "source_generations").glob("*"))
    (working / "approved_geometry_links.json").write_text("{}", encoding="utf-8")
    (working / "review_center_state.json").write_text("{}", encoding="utf-8")
    project.extensions["latest_batch_report_path"] = "old/report.json"
    manager.save(project)

    manager.import_dxf_source(project, source_b)
    second = current_identity(project)

    assert second.source_generation_id != first.source_generation_id
    assert second.analysis_run_id == ""
    assert second.source_sha256 != first.source_sha256
    assert "latest_batch_report_path" not in project.extensions
    assert not (working / "approved_geometry_links.json").exists()
    assert not (working / "review_center_state.json").exists()
    archives_after = set((working / "source_generations").glob("*"))
    new_archives = archives_after - archives_before
    assert len(new_archives) == 1
    archive = next(iter(new_archives))
    assert (archive / "approved_geometry_links.json").is_file()
    assert (archive / "review_center_state.json").is_file()
    assert (archive / "archive_manifest.json").is_file()


def test_first_import_stamps_initial_working_scaffolding(tmp_path: Path) -> None:
    manager = ProjectManager()
    project = manager.create_project(tmp_path, "FirstImport")
    source = tmp_path / "first.dxf"
    source.write_bytes(b"FIRST-DXF")

    manager.import_dxf_source(project, source)
    identity = current_identity(project).to_dict()
    working = Path(project.root) / "working"

    for filename in (
        "detected_positions.json",
        "manual_corrections.json",
        "traceability.json",
        "layer_classification.json",
    ):
        payload = json.loads((working / filename).read_text(encoding="utf-8"))
        assert payload[IDENTITY_KEY] == identity


def test_byte_identical_reselect_preserves_source_generation(tmp_path: Path) -> None:
    manager = ProjectManager()
    project = manager.create_project(tmp_path, "SameSource")
    source = tmp_path / "same.dxf"
    source.write_bytes(b"SAME-DXF")
    manager.import_dxf_source(project, source)
    first = current_identity(project)
    marker = Path(project.root) / "working" / "manual_corrections.json"
    before = marker.read_bytes()
    archives_before = set((Path(project.root) / "working" / "source_generations").glob("*"))

    manager.import_dxf_source(project, source)
    second = current_identity(project)

    assert second.source_generation_id == first.source_generation_id
    assert marker.read_bytes() == before
    archives_after = set((Path(project.root) / "working" / "source_generations").glob("*"))
    assert archives_after == archives_before


def test_reimport_of_edited_project_source_path_is_staged_safely(tmp_path: Path) -> None:
    manager = ProjectManager()
    project = manager.create_project(tmp_path, "InPlace")
    external = tmp_path / "first.dxf"
    external.write_bytes(b"FIRST")
    destination = manager.import_dxf_source(project, external)
    first = current_identity(project)

    destination.write_bytes(b"SECOND-EDITED-IN-PLACE")
    manager.import_dxf_source(project, destination)
    second = current_identity(project)

    assert destination.read_bytes() == b"SECOND-EDITED-IN-PLACE"
    assert second.source_generation_id != first.source_generation_id
    assert (destination.parent / "original_previous_1.dxf").read_bytes() == b"SECOND-EDITED-IN-PLACE"
    assert not list(destination.parent.glob(".incoming_source_*.dxf"))


def test_final_control_freezes_beam_assignment_and_export_does_not_recompute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _manager, project, _identity, entities, report_path = _current_project(
        tmp_path, beam=True
    )
    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)

    assert result.full_specification_complete is True
    assert result.freeze_required is True
    assert result.beam_assignments_frozen == 1
    assert Path(result.freeze_manifest_path or "").is_file()
    with Path(result.ledger_path or "").open("r", encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["beam_assignment_frozen"] == "1"
    assert row["beam_name"] == "Гр А. 1"
    assert row["beam_label_handle"] == "BEAM_TITLE"

    import lion_rebar.export.excel_export as excel_export

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("beam assignment was recomputed after Final Control")

    monkeypatch.setattr(excel_export, "_assign_beam_label", _must_not_run)
    frames, positions = load_export_context(project, entities, _engine())
    assert len(frames) == 1
    assert len(positions) == 1
    assert positions[0].beam_name == "Гр А. 1"
    assert positions[0].beam_label_handle == "BEAM_TITLE"


def test_any_mutation_of_a_frozen_artifact_blocks_export(tmp_path: Path) -> None:
    _manager, project, _identity, entities, report_path = _current_project(tmp_path)
    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)
    assert result.full_specification_complete is True

    approved_path = Path(project.root) / "working" / "approved_geometry_links.json"
    approved_path.write_text(approved_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ExcelExportError, match="променен след Финален контрол"):
        load_export_context(project, entities, _engine())


def test_engine_snapshot_is_part_of_the_final_control_freeze(tmp_path: Path) -> None:
    _manager, project, _identity, entities, report_path = _current_project(tmp_path)
    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)
    assert result.full_specification_complete is True
    freeze = json.loads(
        Path(result.freeze_manifest_path or "").read_text(encoding="utf-8")
    )
    roles = {item["role"] for item in freeze["artifacts"]}
    assert len(roles) == 9
    assert "ENGINE_SNAPSHOT" in roles

    snapshot = Path(project.root) / str(project.extensions["engine_snapshot"])
    snapshot.write_text(snapshot.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ExcelExportError, match="ENGINE_SNAPSHOT.*променен"):
        load_export_context(project, entities, _engine())


def test_final_control_rejects_approved_f003_without_export_dimensions(
    tmp_path: Path,
) -> None:
    manager, project, _identity, _entities, report_path = _current_project(tmp_path)
    manager.save_engine_snapshot(project, _f003_engine())

    report = json.loads(report_path.read_text(encoding="utf-8"))
    row = report["rows"][0]
    row.update(
        {
            "annotation_text": "40N8x740",
            "annotation_count": 40,
            "diameter_mm": 8.0,
            "declared_length_cm": 740.0,
            "master_shape_code": "F003",
            "master_shape_name": "ПРЪТ С ДВА ВИНКЕЛА",
        }
    )
    _write_json(report_path, report)

    approved_path = Path(project.root) / "working" / "approved_geometry_links.json"
    approved = json.loads(approved_path.read_text(encoding="utf-8"))
    link = approved["links"][0]
    link.update(
        {
            "annotation_text": "40N8x740",
            "resolved_annotation_text": "40N8x740",
            "annotation_count": 40,
            "production_count": 40,
            "diameter_mm": 8.0,
            "production_diameter_mm": 8.0,
            "declared_length_cm": 740.0,
            "production_length_cm": 740.0,
            "master_shape_code": "F003",
            "master_shape_name": "ПРЪТ С ДВА ВИНКЕЛА",
        }
    )
    link.pop("production_segment_lengths_cm", None)
    link.pop("production_turn_angles_deg", None)
    _write_json(approved_path, approved)

    result = reconcile_project_integrity(
        project.root,
        report_path=report_path,
        apply=True,
    )

    assert result.integrity_passed is False
    assert result.full_specification_complete is False
    assert result.freeze_required is False
    assert "няма доказани сегментни размери за F003" in result.export_preflight_error
    assert any(
        issue.code == "FINAL_EXPORT_PREFLIGHT_FAILED"
        for issue in result.issues
    )
    assert not (Path(project.root) / "working" / "final_control_freeze.json").exists()


def test_stale_batch_run_identity_blocks_final_control(tmp_path: Path) -> None:
    _manager, project, _identity, _entities, report_path = _current_project(
        tmp_path, stale_report_run=True
    )
    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)

    assert result.identity_failures >= 1
    assert result.integrity_passed is False
    assert result.full_specification_complete is False
    assert result.freeze_required is False
    assert any(issue.code == "STALE_BATCH_REPORT" for issue in result.issues)


def test_missing_analysis_run_id_in_report_blocks_final_control(tmp_path: Path) -> None:
    _manager, project, _identity, _entities, report_path = _current_project(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report[IDENTITY_KEY]["analysis_run_id"] = ""
    report["rows"][0][IDENTITY_KEY]["analysis_run_id"] = ""
    _write_json(report_path, report)

    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)

    assert result.identity_failures >= 1
    assert result.full_specification_complete is False
    assert any(issue.code == "STALE_BATCH_REPORT" for issue in result.issues)


def test_mixed_identity_row_blocks_final_control_even_when_report_root_is_current(
    tmp_path: Path,
) -> None:
    _manager, project, _identity, _entities, report_path = _current_project(
        tmp_path, mixed_row_run=True
    )
    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)

    assert result.identity_failures >= 1
    assert result.full_specification_complete is False
    issue = next(issue for issue in result.issues if issue.code == "MIXED_IDENTITY_REPORT_ROWS")
    assert issue.annotation_handles == ["T1"]
    assert issue.details["reasons"]["T1"] == "ANALYSIS_RUN_MISMATCH"


def test_external_batch_report_is_staged_and_export_remains_self_contained(
    tmp_path: Path,
) -> None:
    _manager, project, _identity, entities, report_path = _current_project(tmp_path)
    external_dir = tmp_path / "cloud_review"
    external_dir.mkdir()
    external_report = external_dir / "current_geometry_batch.json"
    external_report.write_bytes(report_path.read_bytes())
    report_path.unlink()

    result = reconcile_project_integrity(
        project.root, report_path=external_report, apply=True
    )

    assert result.full_specification_complete is True
    staged = Path(result.report_path or "")
    assert staged.is_file()
    assert staged == Path(project.root) / "working" / "final_control_sources" / "batch_report.json"
    assert staged.read_bytes() == external_report.read_bytes()
    external_report.unlink()
    _frames, positions = load_export_context(project, entities, _engine())
    assert len(positions) == 1


def test_saved_non_production_engineer_decision_unblocks_final_control(
    tmp_path: Path,
) -> None:
    _manager, project, _identity, _entities, report_path = _current_project(
        tmp_path, with_link=False
    )
    save_engineer_decision(
        project.root,
        {
            "reason_code": "NON_PRODUCTION_POSITION",
            "note": (
                "Проверено в оригиналния DXF: надписът е остатък и не е "
                "реална производствена позиция."
            ),
            "annotation": {"handle": "T1", "text": "2N10x100"},
            "original": {"geometry_handles": []},
            "diagnostic_problem": {"code": "PHANTOM_POSITION"},
        },
    )

    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)

    assert result.full_specification_complete is True
    assert result.non_production_positions == 1
    assert result.unresolved_positions == 0
    assert result.approved_after == 0


def test_non_production_position_requires_complete_engineer_evidence(tmp_path: Path) -> None:
    invalid = {
        "decision": "NON_PRODUCTION_POSITION",
        "action_class": "EXCLUSION",
        "reason_code": "NON_PRODUCTION_POSITION",
        "note": "",
        "evidence": {},
    }
    _manager, project, _identity, _entities, report_path = _current_project(
        tmp_path, with_link=False, state_decision=invalid
    )
    invalid_result = reconcile_project_integrity(
        project.root, report_path=report_path, apply=True
    )
    assert invalid_result.full_specification_complete is False
    assert invalid_result.non_production_positions == 0
    assert invalid_result.unresolved_positions == 1
    assert any(
        issue.code == "NON_PRODUCTION_EVIDENCE_INVALID"
        for issue in invalid_result.issues
    )

    # A note without structured evidence is still not enough to delete a row
    # from the production specification.
    state_path = Path(project.root) / "working" / "review_center_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["decisions"] = {
        "T1": {
            **invalid,
            "note": "Текстова бележка без evidence object.",
        }
    }
    stamp_payload(state, current_identity(project))
    _write_json(state_path, state)
    missing_evidence_result = reconcile_project_integrity(
        project.root, report_path=report_path, apply=True
    )
    assert missing_evidence_result.full_specification_complete is False
    assert missing_evidence_result.non_production_positions == 0

    valid = {
        "decision": "NON_PRODUCTION_POSITION",
        "action_class": "EXCLUSION",
        "reason_code": "NON_PRODUCTION_POSITION",
        "note": "Проверено в оригиналния DXF: надписът не е реална позиция.",
        "evidence": {"original_checked": True},
    }
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["decisions"] = {"T1": valid}
    stamp_payload(state, current_identity(project))
    _write_json(state_path, state)

    valid_result = reconcile_project_integrity(
        project.root, report_path=report_path, apply=True
    )
    assert valid_result.full_specification_complete is True
    assert valid_result.non_production_positions == 1
    assert valid_result.approved_after == 0
    with Path(valid_result.ledger_path or "").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        ledger_row = next(csv.DictReader(stream))
    assert ledger_row["final_status"] == "NON_PRODUCTION_POSITION"
    _frames, positions = load_export_context(project, _entities, _engine())
    assert positions == []


def test_geometryless_engineer_override_is_counted_and_marked_in_ledger(
    tmp_path: Path,
) -> None:
    _manager, project, _identity, entities, report_path = _current_project(
        tmp_path, geometryless=True
    )
    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)

    assert result.full_specification_complete is True
    assert result.engineer_resolved_without_geometry_count == 1
    with Path(result.ledger_path or "").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        row = next(csv.DictReader(stream))
    assert row["geometry_handles_count"] == "0"
    assert row["geometry_provenance"] == "NONE_ENGINEER_OVERRIDE"
    _frames, positions = load_export_context(project, entities, _engine())
    assert len(positions) == 1
    assert positions[0].geometry_handles == ()


def test_geometryless_correction_marker_is_included_in_counter(tmp_path: Path) -> None:
    _manager, project, _identity, _entities, report_path = _current_project(
        tmp_path, geometryless=True
    )
    approved_path = Path(project.root) / "working" / "approved_geometry_links.json"
    payload = json.loads(approved_path.read_text(encoding="utf-8"))
    payload["links"][0]["geometry_provenance"] = "NONE_ENGINEER_CORRECTION"
    _write_json(approved_path, payload)

    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)
    assert result.full_specification_complete is True
    assert result.engineer_resolved_without_geometry_count == 1


def test_engineer_decision_supports_active_shape_after_f008(tmp_path: Path) -> None:
    working = tmp_path / "working"
    working.mkdir(parents=True)
    shape = ShapeDefinition(
        code="F011",
        name="ДИНАМИЧНА ФОРМА",
        state="LOCKED",
        locked=True,
        active=True,
        segments=[
            SegmentDefinition(index=1, length_code="L1"),
            SegmentDefinition(index=2, length_code="L2", bend_after_deg=90.0),
        ],
    )
    retired = ShapeDefinition(code="F012", name="OLD", active=False, state="RETIRED")
    assert [item.code for item in active_production_shapes([retired, shape])] == ["F011"]

    payload = {
        "schema": "lion-rebar-engineer-decision/v2",
        "shape_definition": shape.to_dict(),
        "reason_code": "WRONG_SHAPE",
        "reason": "Грешна MASTER форма",
        "note": "Избрана е активната форма F011 от заключения Engine.",
        "annotation": {
            "handle": "T11",
            "text": "2N10x100",
            "resolved_text": "2N10x100",
        },
        "original": {
            "count": 2,
            "diameter_mm": 10,
            "declared_length_cm": 100,
            "shape_code": "F008",
            "segment_lengths_cm": [100],
            "geometry_handles": ["G11"],
        },
        "resolved": {
            "count": 2,
            "diameter_mm": 10,
            "production_length_cm": 100,
            "segment_lengths_cm": [40, 60],
            "shape_code": "F011",
            "shape_name": "ДИНАМИЧНА ФОРМА",
            "shape_revision": 1,
            "turn_angles_deg": [90],
        },
        "geometry": {
            "handles": ["G11"],
            "layer": "Arm - N10",
            "basis": "OWNER_SELECTED_DXF_GEOMETRY",
            "notes": [],
        },
        "diagnostic_problem": {},
    }
    output = save_engineer_decision(tmp_path, payload)
    approved = json.loads(output.read_text(encoding="utf-8"))
    assert approved["links"][0]["master_shape_code"] == "F011"
    assert approved["links"][0]["engine_shape_parameter_source"] == "PINNED_ENGINE_SHAPE_DEFINITION"


def test_auto_approval_rejects_mixed_report_row_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    (project_root / "working").mkdir(parents=True)
    identity = DataIdentity(
        project_id="P-001",
        source_sha256="a" * 64,
        source_generation_id="GEN-001",
        analysis_run_id="RUN-001",
    )
    stale_row_identity = DataIdentity(
        project_id=identity.project_id,
        source_sha256=identity.source_sha256,
        source_generation_id=identity.source_generation_id,
        analysis_run_id="RUN-OLD",
    )
    row = {
        "annotation_handle": "T-MIXED",
        "annotation_text": "2N10x100",
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "candidate_confidence": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "master_shape_code": "F001",
        "master_shape_name": "ПРАВ ПРЪТ",
    }
    stamp_payload(row, stale_row_identity)
    report = {
        "schema": "lion-rebar-geometry-batch-report/v3",
        "rows": [row],
    }
    stamp_payload(report, identity)
    report_path = tmp_path / "mixed_geometry_batch.json"
    _write_json(report_path, report)

    fake_position = SimpleNamespace(
        annotation_handle="T-MIXED",
        geometry_handles=("G-MIXED",),
        production_length_cm=100.0,
        production_length_source="ANNOTATION",
        shape_code="F001",
        shape_name="ПРАВ ПРЪТ",
    )
    monkeypatch.setattr(
        auto_approval,
        "build_batch_export_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            positions=(fake_position,),
            blockers=(),
            actionable_count=1,
        ),
    )

    summary = auto_approve_gate_positions(
        project_root,
        report_path=report_path,
        entities=[],
        engine=SimpleNamespace(version="0.3.13"),
        data_identity=identity,
    )

    assert summary.failed is True
    assert summary.blocked_count == 1
    assert summary.blocked_handles == ("T-MIXED",)
    assert "друг project/source/run" in summary.error
    assert not (project_root / "working" / "approved_geometry_links.json").exists()


def test_real_pinned_engine_exposes_all_active_shapes_after_f008() -> None:
    bundle = Path(
        "engine_updates/"
        "lion-rebar-master_v0.3.13_F003_F005_DISTINCT_CONTEXT_PRODUCTION_DRAFT.lionengine"
    )
    engine, _manifest, _members = inspect_engine_bundle(bundle)
    active_codes = [shape.code for shape in active_production_shapes(engine.shapes)]

    assert active_codes == [
        "F001",
        "F002",
        "F003",
        "F004",
        "F005",
        "F006",
        "F007",
        "F008",
        "F010",
        "F011",
        "F012",
        "F013",
        "F014",
        "F016",
        "F019",
    ]
    assert all(code not in active_codes for code in ("F009", "F015", "F017", "F018"))



def test_save_non_production_decision_writes_explicit_original_dxf_evidence(
    tmp_path: Path,
) -> None:
    payload = {
        "schema": "lion-rebar-engineer-decision/v2",
        "reason_code": "NON_PRODUCTION_POSITION",
        "reason": "Не е реална производствена позиция",
        "note": "Проверено в оригиналния DXF: този надпис не е част от чертежа.",
        "annotation": {
            "handle": "T-PHANTOM",
            "text": "60N8x130",
            "resolved_text": "",
        },
        "original": {
            "count": 60,
            "diameter_mm": 8,
            "declared_length_cm": 130,
            "shape_code": "F005",
            "geometry_handles": ["G-FOREIGN"],
        },
        "resolved": {},
        "geometry": {"handles": []},
        "diagnostic_problem": {
            "reason_code": "WRONG_OBJECT",
            "reason": "Останал надпис от друг работен чертеж",
        },
    }

    state_path = save_engineer_decision(tmp_path, payload)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    decision = state["decisions"]["T-PHANTOM"]
    assert decision["decision"] == "NON_PRODUCTION_POSITION"
    assert decision["action_class"] == "EXCLUSION"
    assert decision["evidence"]["evidence_type"] == "OWNER_ENGINEER_ORIGINAL_DXF_CHECK"
    assert decision["evidence"]["original_dxf_checked"] is True
    assert decision["evidence"]["operator_note"] == payload["note"]

    approved_path = tmp_path / "working" / "approved_geometry_links.json"
    approved = json.loads(approved_path.read_text(encoding="utf-8"))
    assert approved["links"] == []
