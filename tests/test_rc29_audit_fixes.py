"""Регресии за възпроизводимите находки от независимия rc28 одит."""
from __future__ import annotations

import json
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace

from lion_rebar.dxf.batch_geometry_report import (
    _apply_dimension_derived_production_sizes,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.final_export_manifest import (
    build_final_export_manifest,
    verify_final_export_manifest,
    write_final_export_manifest,
)
from lion_rebar.export.frozen_export_config import (
    FrozenFrameMapping,
    write_frozen_config,
)
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.project.integrity import _build_freeze_manifest
from lion_rebar.project.integrity import _freeze_beam_assignments
from lion_rebar.export.frame_decisions import record_frame_decision
from lion_rebar.review.apply_to_group import (
    GroupApplicationError,
    apply_group_plan,
    build_group_plan,
    undo_group_operation,
)


def _export_files(root: Path, *, trace_code: str = "VERTICALS") -> dict:
    working = root / "working"
    working.mkdir(parents=True, exist_ok=True)
    config = write_frozen_config(root, [
        FrozenFrameMapping(1, "M1", "ВЕРТИКАЛИ", "VERTICALS", "вертикали")
    ])
    ledger = root / "ledger.csv"; ledger.write_text("a\n", encoding="utf-8")
    excel = root / "spec.xlsx"; excel.write_bytes(b"PK demo")
    dxf = root / "spec.dxf"; dxf.write_text("0\nEOF\n", encoding="utf-8")
    excel_trace = root / "spec_TRACEABILITY.json"
    excel_trace.write_text(json.dumps({
        "schema": "lion-rebar-excel-traceability/v2",
        "frames": [{
            "frame_index": 1,
            "category_code": trace_code,
            "sheet_name": "вертикали" if trace_code == "VERTICALS" else "долна",
        }],
    }), encoding="utf-8")
    dxf_trace = root / "spec_DXF_TRACEABILITY.json"
    dxf_trace.write_text("{}", encoding="utf-8")
    freeze = root / "working" / "final_control_freeze.json"
    freeze.write_text(json.dumps({
        "schema": "lion-rebar-final-control-freeze/v1", "status": "VALID",
        "artifacts": [{"role": "EXPORT_CONFIG",
                       "sha256": hashlib.sha256(config.read_bytes()).hexdigest()}],
    }), encoding="utf-8")
    return dict(
        project_root=root, export_config_path=config,
        final_ledger_path=ledger, excel_path=excel, final_dxf_path=dxf,
        excel_trace_path=excel_trace, dxf_trace_path=dxf_trace,
        final_control_path=freeze,
    )


def _manifest(root: Path, **paths) -> dict:
    return build_final_export_manifest(
        app_version="0.1.44.20-rc29", engine_id="E", engine_version="V",
        engine_sha256="a" * 64, matcher_sha256="b" * 64,
        project_id="P", source_sha256="c" * 64,
        source_generation_id="G", analysis_run_id="R", final_control_id="R",
        ledger_row_count=1, excel_row_count=1, **paths,
    )


def test_real_frames_trace_mismatch_fails_final_manifest():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manifest = _manifest(root, **_export_files(root, trace_code="BOTTOM"))
        path = write_final_export_manifest(root, manifest)
        problems = verify_final_export_manifest(path)
        assert any("EXPORT_SHEET_MISMATCH" in item for item in problems)
        assert any("EXPORT_CATEGORY_MISMATCH" in item for item in problems)


def test_missing_excel_trace_is_a_manifest_failure():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        paths = _export_files(root)
        paths["excel_trace_path"].unlink()
        path = write_final_export_manifest(root, _manifest(root, **paths))
        problems = verify_final_export_manifest(path)
        assert "ARTIFACT_MISSING: EXCEL_TRACE" in problems
        assert any("EXCEL_TRACE_NOT_ON_DISK" in item for item in problems)


def test_freeze_manifest_hashes_export_config_as_required_artifact():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        files = []
        for index in range(7):
            path = root / f"input_{index}.json"
            path.write_text(str(index), encoding="utf-8")
            files.append((f"ROLE_{index}", path))
        identity = DataIdentity("P", "s" * 64, "G", "R")
        frame = SimpleNamespace(
            index=1, marker_handle="M1", title_text="ВЕРТИКАЛИ",
            category_code="VERTICALS", suggested_sheet_name="вертикали",
        )
        manifest = _build_freeze_manifest(
            root=root, manifest_path=root / "working" / "freeze.json",
            files=files, identity=identity, frames=[frame],
        )
        config = next(
            item for item in manifest["artifacts"]
            if item["role"] == "EXPORT_CONFIG"
        )
        assert config["sha256"]
        assert manifest["export_config_fingerprint"]


def test_operator_unknown_decision_reaches_final_ledger_frame_freeze():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        identity = DataIdentity("P", "s" * 64, "G", "R")
        record_frame_decision(
            root, marker_handle="M1", category_code="VERTICALS",
            decided_by="инженер", note="вертикални елементи",
            identity=identity, frame_is_unknown=True,
        )
        extracted = {"entities": [
            {"handle": "M1", "entity_type": "TEXT", "layer": "$Ramka",
             "geometry": {"insert": [0, 0, 0]}, "text": "ЧЕРТЕЖ:"},
            {"handle": "VS1", "entity_type": "TEXT", "layer": "DETAIL",
             "geometry": {
                 "insert": [100, 800, 0],
                 "visual_anchor": [250, 815, 0],
                 "visual_bbox": [
                     [0, 800, 0], [500, 800, 0],
                     [0, 830, 0], [500, 830, 0],
                 ],
             },
             "text": "Тип 1 - 100x25 - 1 бр.(Сх.арм.1)"},
            {"handle": "VD1", "entity_type": "LINE", "layer": "DETAIL",
             "geometry": {
                 "start": [0, 780, 0], "end": [500, 780, 0],
             }},
            {"handle": "T1", "entity_type": "BLOCK_ATTR_TEXT",
             "layer": "ARM_TXT", "geometry": {"insert": [100, 500, 0]},
             "text": "2N8x100"},
        ]}
        assignments, issues = _freeze_beam_assignments(
            rows=[{"annotation_class": "ACTIONABLE",
                   "annotation_handle": "T1"}],
            links_by_annotation={"T1": {"annotation_handle": "T1"}},
            extracted_payload=extracted, project_root=root, identity=identity,
        )
        assert issues == []
        assert assignments["T1"]["frame_category_code"] == "VERTICALS"
        assert assignments["T1"]["vertical_assignment_frozen"] is True
        assert assignments["T1"]["vertical_section_kind"] == "TYPE"


def _owned_f006_case(*, width: float, height: float, hook: float, total: float):
    entities = [
        DxfEntityRecord("A", "TEXT", "L", {"insert": [0, 0, 0]}),
        DxfEntityRecord("C", "LWPOLYLINE", "L", {"points": [
            [0, 0, 0], [width, 0, 0], [width, height, 0],
            [width - hook, height, 0], [width - hook, 0, 0],
        ]}),
        DxfEntityRecord("DW", "DIMENSION", "L", {
            "measurement": width, "defpoint2": [0, 0, 0],
            "defpoint3": [width, 0, 0], "text_midpoint": [width / 2, 0, 0]}),
        DxfEntityRecord("DH", "DIMENSION", "L", {
            "measurement": height, "defpoint2": [width, 0, 0],
            "defpoint3": [width, height, 0], "text_midpoint": [width, height / 2, 0]}),
        DxfEntityRecord("DK", "DIMENSION", "L", {
            "measurement": hook, "defpoint2": [width, height, 0],
            "defpoint3": [width - hook, height, 0],
            "text_midpoint": [width - hook / 2, height, 0]}),
    ]
    row = {
        "annotation_handle": "A", "master_shape_code": "F006",
        "declared_length_cm": total, "candidate_handles": ["C"],
        "candidate_notes": [],
    }
    _apply_dimension_derived_production_sizes([row], entities)
    return row


def test_owned_dimensions_320_vs_135_remain_blocked():
    row = _owned_f006_case(width=100, height=50, hook=10, total=135)
    assert row["f006_production_status"] == "F006_PRODUCTION_REVIEW"
    assert row["production_export_blocked"] is True
    assert "production_segments_cm" not in row


def test_owned_dimensions_allow_the_central_bend_allowance():
    row = _owned_f006_case(width=35, height=20, hook=10, total=135)
    assert row["f006_production_status"] == "F006_PRODUCTION_DERIVED"
    assert row["production_export_blocked"] is False
    assert sum(row["production_segments_cm"]) == 130


IDENTITY = DataIdentity("P", "s" * 64, "G", "R")


def _row(handle: str) -> dict:
    return {
        "annotation_handle": handle, "annotation_text": "25N8x135",
        "annotation_count": 25, "diameter_mm": 8, "declared_length_cm": 135,
        "master_shape_code": "F006", "candidate_handles": [f"G{handle}"],
        "canonical_segment_lengths_cm": [35, 20, 10],
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД", "review_group_key": "K",
        "project_id": "P", "source_sha256": "s" * 64,
        "source_generation_id": "G", "analysis_run_id": "R",
    }


def _apply(root: Path, suffix: str) -> str:
    rows = [_row(f"A{suffix}"), _row(f"B{suffix}")]
    plan = build_group_plan(
        rows[0], rows, IDENTITY, changes={"master_shape_code": "F006"}
    )
    return apply_group_plan(
        root, plan, {row["annotation_handle"]: row for row in rows}, IDENTITY,
        applied_by="инженер", note="тест",
        save_decision=lambda *_args: None,
    )


def test_undo_automatically_refuses_after_verified_export():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        operation_id = _apply(root, "1")
        operation = json.loads(
            (root / "working" / "group_operations.jsonl").read_text().splitlines()[-1]
        )
        history = root / "working" / "production_export_history.jsonl"
        history.write_text(json.dumps({
            "event": "PRODUCTION_VERIFIED",
            "at_unix_ns": int(operation["at_unix_ns"]) + 1,
        }) + "\n", encoding="utf-8")
        try:
            undo_group_operation(
                root, operation_id, undone_by="инженер",
                save_decision=lambda *_args: None,
            )
        except GroupApplicationError as error:
            assert "production export" in str(error)
            return
        raise AssertionError("Undo след verified export трябва да бъде отказан")


def test_backend_refuses_undo_of_non_latest_group_operation():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        older = _apply(root, "1")
        _apply(root, "2")
        try:
            undo_group_operation(
                root, older, undone_by="инженер",
                save_decision=lambda *_args: None,
            )
        except GroupApplicationError as error:
            assert "последното активно" in str(error)
            return
        raise AssertionError("Undo на по-стара операция трябва да бъде отказан")
