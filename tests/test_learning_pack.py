from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from lion_rebar.session.learning_pack import (
    build_learning_pack,
    publish_learning_pack,
    verify_learning_pack,
)


def test_learning_pack_contains_journal_candidates_and_manifest(tmp_path: Path) -> None:
    events = [
        {
            "event_id": 1,
            "session_id": "S-TEST",
            "at": "2026-08-12T08:00:00+03:00",
            "event_type": "APPROVED",
            "severity": "INFO",
            "project_name": "P1",
            "project_root": "",
            "source_sha256": "dxf",
            "engine_id": "lion-rebar-master",
            "engine_version": "0.3.13",
            "engine_sha256": "engine",
            "matcher_sha256": "matcher",
            "annotation_handle": "A1",
            "annotation_text": "29N8x94",
            "report_path": None,
            "payload": {
                "annotation_handle": "A1",
                "annotation_text": "29N8x94",
                "row_context": {
                    "master_shape_code": "F005",
                    "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
                    "matcher_disposition": "MANUAL",
                    "canonical_segment_lengths_cm": [40, 14, 40],
                },
            },
        }
    ]
    result = build_learning_pack(
        session_id="S-TEST",
        started_at="2026-08-12T08:00:00+03:00",
        ended_at="2026-08-12T18:00:00+03:00",
        events=events,
        projects=[],
        output_dir=tmp_path,
    )
    assert result.path.is_file()
    with zipfile.ZipFile(result.path) as archive:
        names = set(archive.namelist())
        assert "session/summary.json" in names
        assert "session/decision_journal.csv" in names
        assert "session/session_database_extract.json" in names
        assert "session/rule_candidates.json" in names
        assert "session/calibration_evidence.json" in names
        assert "START_PROMPT_BG.md" in names
        assert "manifest.json" in names
        candidates = json.loads(archive.read("session/rule_candidates.json"))
        assert candidates["candidates"] == []
        calibration = json.loads(archive.read("session/calibration_evidence.json"))
        assert calibration["groups"] == calibration["confirmations"]
        assert len(calibration["groups"]) == 1
        assert calibration["groups"][0]["accepted_shape_code"] == "F005"
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema"] == "lion-rebar-learning-pack/v1"


def test_learning_pack_expands_bulk_decisions_to_individual_journal_rows(tmp_path: Path) -> None:
    events = [
        {
            "event_id": 1,
            "session_id": "S-BULK",
            "at": "2026-08-12T08:00:00+03:00",
            "event_type": "BULK_APPROVED",
            "severity": "INFO",
            "project_name": "P1",
            "project_root": "",
            "source_sha256": "dxf",
            "engine_id": "lion-rebar-master",
            "engine_version": "0.3.13",
            "engine_sha256": "engine",
            "matcher_sha256": "matcher",
            "annotation_handle": None,
            "annotation_text": None,
            "report_path": None,
            "payload": {
                "mode": "GEOMETRY_SAMPLE",
                "decisions": [
                    {
                        "annotation_handle": "A1",
                        "annotation_text": "2N12x700",
                        "master_shape_code": "F003",
                        "canonical_segment_lengths_cm": [25, 650, 25],
                    },
                    {
                        "annotation_handle": "A2",
                        "annotation_text": "4N8x160",
                        "master_shape_code": "F006",
                        "canonical_segment_lengths_cm": [10, 50, 20, 50, 20, 10],
                    },
                ],
            },
        }
    ]
    result = build_learning_pack(
        session_id="S-BULK",
        started_at="2026-08-12T08:00:00+03:00",
        ended_at="2026-08-12T18:00:00+03:00",
        events=events,
        projects=[],
        output_dir=tmp_path,
    )
    with zipfile.ZipFile(result.path) as archive:
        journal = json.loads(archive.read("session/decision_journal.json"))
        assert [row["annotation_handle"] for row in journal["decisions"]] == ["A1", "A2"]
        summary = json.loads(archive.read("session/summary.json"))
        assert summary["decision_count"] == 2
        assert summary["decision_action_counts"] == {"CONFIRMATION": 2}
        candidates = json.loads(archive.read("session/rule_candidates.json"))
        assert candidates["candidates"] == []


def test_learning_pack_manifest_verification_rejects_corrupt_copy(tmp_path: Path) -> None:
    result = build_learning_pack(
        session_id="S-VERIFY",
        started_at="2026-08-12T08:00:00+03:00",
        ended_at="2026-08-12T18:00:00+03:00",
        events=[],
        projects=[],
        output_dir=tmp_path / "local",
    )
    manifest = verify_learning_pack(result.path)
    assert manifest["schema"] == "lion-rebar-learning-pack/v1"

    corrupt = tmp_path / "not-a-zip.zip"
    corrupt.write_bytes(b"File is not a zip file")
    with pytest.raises(RuntimeError, match="не е валиден ZIP"):
        verify_learning_pack(corrupt)


def test_learning_pack_publication_is_secondary_and_verified(tmp_path: Path) -> None:
    result = build_learning_pack(
        session_id="S-PUBLISH",
        started_at="2026-08-12T08:00:00+03:00",
        ended_at="2026-08-12T18:00:00+03:00",
        events=[],
        projects=[],
        output_dir=tmp_path / "local",
    )
    published = publish_learning_pack(
        result,
        destination_dir=tmp_path / "cloud",
    )
    assert published.publication_warning is None
    assert published.published_path is not None
    assert published.published_latest_path is not None
    verify_learning_pack(published.published_path)
    verify_learning_pack(published.published_latest_path)
    assert published.path.parent == tmp_path / "local"


def test_learning_pack_publication_failure_keeps_local_pack_valid(tmp_path: Path) -> None:
    result = build_learning_pack(
        session_id="S-PUBLISH-FAIL",
        started_at="2026-08-12T08:00:00+03:00",
        ended_at="2026-08-12T18:00:00+03:00",
        events=[],
        projects=[],
        output_dir=tmp_path / "local",
    )
    invalid_destination = tmp_path / "cloud-is-a-file"
    invalid_destination.write_text("not a directory", encoding="utf-8")
    published = publish_learning_pack(
        result,
        destination_dir=invalid_destination,
    )
    assert published.publication_warning
    assert published.published_path is None
    assert published.warning_count == result.warning_count + 1
    verify_learning_pack(published.path)
    verify_learning_pack(published.latest_path)


def test_learning_pack_replaces_preexisting_corrupt_latest_atomically(tmp_path: Path) -> None:
    output = tmp_path / "local"
    output.mkdir(parents=True)
    latest = output / "LION_REBAR_LEARNING_PACK_LATEST.zip"
    latest.write_bytes(b"File is not a zip file")

    result = build_learning_pack(
        session_id="S-REPLACE-CORRUPT",
        started_at="2026-08-12T08:00:00+03:00",
        ended_at="2026-08-12T18:00:00+03:00",
        events=[],
        projects=[],
        output_dir=output,
    )

    assert result.latest_path == latest
    verify_learning_pack(result.path)
    verify_learning_pack(result.latest_path)
    assert result.path.read_bytes() == result.latest_path.read_bytes()


def test_learning_pack_archive_is_verified_before_filesystem_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from lion_rebar.session import learning_pack as module

    calls: list[str] = []
    original = module.verify_learning_pack_bytes

    def tracked(payload: bytes):
        calls.append("memory_verified")
        return original(payload)

    monkeypatch.setattr(module, "verify_learning_pack_bytes", tracked)
    result = build_learning_pack(
        session_id="S-MEMORY-FIRST",
        started_at="2026-08-12T08:00:00+03:00",
        ended_at="2026-08-12T18:00:00+03:00",
        events=[],
        projects=[],
        output_dir=tmp_path / "local",
    )

    assert calls == ["memory_verified"]
    verify_learning_pack(result.path)


def test_learning_pack_separates_confirmations_from_correction_rule_candidates(tmp_path: Path) -> None:
    events = [
        {
            "event_id": 1,
            "session_id": "S-INTEL",
            "at": "2026-08-12T08:00:00+03:00",
            "event_type": "APPROVED",
            "severity": "INFO",
            "project_name": "P1",
            "project_root": "",
            "source_sha256": "dxf-1",
            "engine_version": "0.3.13",
            "matcher_sha256": "matcher",
            "annotation_handle": "A1",
            "annotation_text": "2N12x700",
            "payload": {
                "annotation_handle": "A1",
                "annotation_text": "2N12x700",
                "row_context": {
                    "annotation_count": 2,
                    "diameter_mm": 12,
                    "declared_length_cm": 700,
                    "master_shape_code": "F003",
                    "master_shape_name": "ПРЪТ С ДВА ВИНКЕЛА",
                    "candidate_handles": ["G1", "G2", "G3"],
                    "canonical_segment_lengths_cm": [20, 660, 20],
                    "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
                    "matcher_disposition": "CONTROL",
                },
            },
        },
        {
            "event_id": 2,
            "session_id": "S-INTEL",
            "at": "2026-08-12T08:05:00+03:00",
            "event_type": "ENGINEER_RESOLVED",
            "severity": "INFO",
            "project_name": "P1",
            "project_root": "",
            "source_sha256": "dxf-1",
            "engine_version": "0.3.13",
            "matcher_sha256": "matcher",
            "annotation_handle": "A2",
            "annotation_text": "60N8x130",
            "payload": {
                "annotation_handle": "A2",
                "annotation_text": "60N8x130",
                "resolved_annotation_text": "60N8x130",
                "action_class": "CORRECTION",
                "reason_code": "WRONG_SHAPE",
                "reason": "Грешна MASTER форма",
                "note": "Локалният надпис фиби определя F005.",
                "original": {
                    "count": 60,
                    "diameter_mm": 8,
                    "declared_length_cm": 130,
                    "shape_code": "F003",
                    "segment_lengths_cm": [40, 50, 40],
                    "geometry_handles": ["U1", "U2", "U3"],
                },
                "resolved": {
                    "count": 60,
                    "diameter_mm": 8,
                    "production_length_cm": 130,
                    "shape_code": "F005",
                    "shape_name": "ФИБА",
                    "segment_lengths_cm": [40, 50, 40],
                },
                "geometry": {"handles": ["U1", "U2", "U3"]},
                "row_context": {
                    "master_shape_code": "F003",
                    "candidate_handles": ["U1", "U2", "U3"],
                    "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
                    "matcher_disposition": "MANUAL",
                },
            },
        },
    ]
    result = build_learning_pack(
        session_id="S-INTEL",
        started_at="2026-08-12T08:00:00+03:00",
        ended_at="2026-08-12T18:00:00+03:00",
        events=events,
        projects=[],
        output_dir=tmp_path,
    )
    with zipfile.ZipFile(result.path) as archive:
        journal = json.loads(archive.read("session/decision_journal.json"))["decisions"]
        assert journal[0]["action_class"] == "CONFIRMATION"
        assert journal[0]["accepted_shape_code"] == "F003"
        assert journal[1]["action_class"] == "CORRECTION"
        assert journal[1]["accepted_shape_code"] == "F005"
        calibration_payload = json.loads(archive.read("session/calibration_evidence.json"))
        assert calibration_payload["groups"] == calibration_payload["confirmations"]
        calibration = calibration_payload["groups"]
        assert len(calibration) == 1
        assert calibration[0]["accepted_shape_code"] == "F003"
        candidates = json.loads(archive.read("session/rule_candidates.json"))["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["candidate_type"] == "SHAPE_CORRECTION"
        assert candidates[0]["maturity"] == "OBSERVATION"
        assert candidates[0]["accepted_shape_code"] == "F005"


def test_learning_pack_project_completion_states_are_explicit(tmp_path: Path) -> None:
    root_complete = tmp_path / "P-complete"
    root_no_control = tmp_path / "P-no-control"
    root_complete.mkdir()
    root_no_control.mkdir()
    events = [
        {
            "event_id": 1,
            "session_id": "S-STATUS",
            "at": "2026-08-12T10:00:00+03:00",
            "event_type": "FINAL_INTEGRITY",
            "severity": "INFO",
            "project_name": "P-complete",
            "project_root": str(root_complete),
            "source_sha256": "a",
            "payload": {
                "integrity_passed": True,
                "full_specification_complete": True,
                "unresolved_positions": 0,
                "explicit_exclusions": 0,
                "intrinsic_missing_master": 0,
                "duplicate_groups_remaining": 0,
            },
        }
    ]
    projects = [
        {"project_name": "P-complete", "project_root": str(root_complete), "source_sha256": "a"},
        {"project_name": "P-no-control", "project_root": str(root_no_control), "source_sha256": "b"},
    ]
    result = build_learning_pack(
        session_id="S-STATUS",
        started_at="2026-08-12T08:00:00+03:00",
        ended_at="2026-08-12T18:00:00+03:00",
        events=events,
        projects=projects,
        output_dir=tmp_path / "out",
    )
    with zipfile.ZipFile(result.path) as archive:
        summary = json.loads(archive.read("session/summary.json"))
    by_name = {row["project_name"]: row for row in summary["projects"]}
    assert by_name["P-complete"]["completion_status"] == "COMPLETE"
    assert by_name["P-no-control"]["completion_status"] == "NO_FINAL_CONTROL"


def test_learning_pack_preserves_operator_correct_specification_text(tmp_path: Path) -> None:
    events = [
        {
            "event_id": 1,
            "session_id": "S-CORRECT-TEXT",
            "at": "2026-08-13T08:00:00+03:00",
            "event_type": "PROBLEM_RECORDED",
            "severity": "INFO",
            "project_name": "P1",
            "project_root": "",
            "source_sha256": "dxf",
            "engine_version": "0.3.13",
            "matcher_sha256": "matcher",
            "annotation_handle": "A1",
            "annotation_text": "60N8x130",
            "payload": {
                "annotation_handle": "A1",
                "decision": {
                    "decision": "PROBLEM_RECORDED",
                    "action_class": "PROBLEM",
                    "reason_code": "WRONG_SHAPE",
                    "reason": "Грешна MASTER форма",
                    "correct_specification": "F005, 60N8x130, 40+50+40",
                    "note": "Локалният надпис фиби определя формата.",
                },
                "row_context": {
                    "annotation_text": "60N8x130",
                    "master_shape_code": "F003",
                    "candidate_handles": ["G1", "G2", "G3"],
                    "matcher_disposition": "MANUAL",
                },
            },
        }
    ]
    result = build_learning_pack(
        session_id="S-CORRECT-TEXT",
        started_at="2026-08-13T08:00:00+03:00",
        ended_at="2026-08-13T18:00:00+03:00",
        events=events,
        projects=[],
        output_dir=tmp_path,
    )
    with zipfile.ZipFile(result.path) as archive:
        decisions = json.loads(archive.read("session/decision_journal.json"))["decisions"]
        csv_text = archive.read("session/decision_journal.csv").decode("utf-8-sig")
    assert decisions[0]["correct_specification"] == "F005, 60N8x130, 40+50+40"
    assert "F005, 60N8x130, 40+50+40" in csv_text
