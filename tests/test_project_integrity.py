from __future__ import annotations

import json
import hashlib
from pathlib import Path

from lion_rebar.project.integrity import reconcile_project_integrity


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _line(handle: str, layer: str, start: tuple[float, float], end: tuple[float, float]) -> dict:
    return {
        "handle": handle,
        "entity_type": "LINE",
        "layer": layer,
        "geometry": {"start": [*start, 0.0], "end": [*end, 0.0]},
    }


def _annotation(handle: str, point: tuple[float, float]) -> dict:
    return {
        "handle": handle,
        "entity_type": "BLOCK_ATTR_TEXT",
        "layer": "ARM_TXT",
        "geometry": {"visual_anchor": [*point, 0.0]},
    }


def _link(annotation: str, text: str, geometry: str, *, layer: str, length: float) -> dict:
    return {
        "annotation_handle": annotation,
        "annotation_text": text,
        "diameter_mm": 10.0,
        "declared_length_cm": length,
        "geometry_handles": [geometry],
        "geometry_layer": layer,
        "segment_count": 1,
        "geometry_length_raw": length,
        "numeric_difference": 0.0,
        "distance_raw": 8.0,
        "approval_status": "ОДОБРЕНО РЪЧНО",
    }


def _report_row(handle: str, text: str) -> dict:
    return {
        "annotation_handle": handle,
        "annotation_text": text,
        "annotation_class": "ACTIONABLE",
        "batch_status": "ОДОБРЕНА ВРЪЗКА",
        "candidate_handles": [],
        "master_shape_code": "F001",
    }


def _valid_f006_production_contract() -> dict:
    segments = [10.0, 20.0, 20.0, 20.0, 20.0, 10.0]
    return {
        "declared_length_cm": 105.0,
        "f006_production_status": "F006_PRODUCTION_DERIVED",
        "f006_production_reason": "OWNED_DIMENSIONS",
        "canonical_segment_lengths_cm": segments,
        "canonical_turn_angles_deg": [90.0, 90.0, 90.0, 90.0],
        "production_segments_cm": segments,
        "production_segment_evidence": {
            "shape_code": "F006",
            "segments": segments,
            "turn_angles_deg": [90.0, 90.0, 90.0, 90.0],
            "source": "F006_OWNED_CONTOUR_DIMENSIONS",
            "confidence": "HIGH",
            "owner_geometry_handles": ["G1"],
            "dimension_handles": ["D-SIDE", "D-HOOK"],
            "dimension_measurements_cm": [
                {"handle": "D-SIDE", "measurement_cm": 20.0},
                {"handle": "D-HOOK", "measurement_cm": 10.0},
            ],
        },
    }


def test_integrity_revokes_problem_approval_and_keeps_position_unresolved(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    working = root / "working"
    _write(
        working / "approved_geometry_links.json",
        {
            "project_name": "Project",
            "links": [_link("A1", "2N10x100", "L1", layer="Arm - N10", length=100.0)],
            "history": [],
        },
    )
    _write(
        working / "review_center_state.json",
        {
            "decisions": {
                "A1": {
                    "decision": "NO_VALID_CANDIDATE",
                    "reason": "Маркира чужда позиция",
                }
            }
        },
    )
    _write(
        working / "extracted_entities.json",
        {"entities": [_annotation("A1", (10.0, 10.0)), _line("L1", "Arm - N10", (0.0, 0.0), (100.0, 0.0))]},
    )
    report = tmp_path / "report.json"
    _write(report, {"rows": [_report_row("A1", "2N10x100")]})

    result = reconcile_project_integrity(root, report_path=report, apply=True)

    approved = json.loads((working / "approved_geometry_links.json").read_text(encoding="utf-8"))
    assert approved["links"] == []
    assert result.stale_approvals_revoked == 1
    assert result.approved_after == 0
    assert result.accounted_positions == 0
    assert result.unresolved_positions == 1
    assert result.safe_confirmation_pending == 0
    assert result.engineering_decisions_pending == 1
    assert result.integrity_passed is False


def test_integrity_reassigns_duplicate_single_lines_to_unique_nearest_geometry(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    working = root / "working"
    links = [
        _link("A1", "39N10x950", "G_SHARED", layer="Arm - N10", length=950.0),
        _link("A2", "7N10x950", "G_SHARED", layer="Arm - N10", length=950.0),
        _link("B1", "12N12x240", "H_SHARED", layer="Arm - N12", length=240.0),
        _link("B2", "12N12x240", "H_SHARED", layer="Arm - N12", length=240.0),
    ]
    _write(
        working / "approved_geometry_links.json",
        {"project_name": "Project", "links": links, "history": []},
    )
    _write(working / "review_center_state.json", {"decisions": {}})
    entities = [
        _annotation("A1", (250.0, 16.0)),
        _annotation("A2", (16.0, 250.0)),
        _line("G_SHARED", "Arm - N10", (0.0, 0.0), (950.0, 0.0)),
        _line("G_ALT", "Arm - N10", (0.0, 250.0), (950.0, 250.0)),
        _annotation("B1", (120.0, 16.0)),
        _annotation("B2", (316.0, 120.0)),
        _line("H_SHARED", "Arm - N12", (0.0, 0.0), (240.0, 0.0)),
        _line("H_ALT", "Arm - N12", (316.0, 0.0), (316.0, 240.0)),
    ]
    _write(working / "extracted_entities.json", {"entities": entities})
    report = tmp_path / "report.json"
    _write(
        report,
        {"rows": [_report_row("A1", "39N10x950"), _report_row("A2", "7N10x950"), _report_row("B1", "12N12x240"), _report_row("B2", "12N12x240")]},
    )

    result = reconcile_project_integrity(root, report_path=report, apply=True)

    approved = json.loads((working / "approved_geometry_links.json").read_text(encoding="utf-8"))
    by_annotation = {row["annotation_handle"]: row for row in approved["links"]}
    assert by_annotation["A1"]["geometry_handles"] == ["G_SHARED"]
    assert by_annotation["A2"]["geometry_handles"] == ["G_ALT"]
    assert by_annotation["B1"]["geometry_handles"] == ["H_SHARED"]
    assert by_annotation["B2"]["geometry_handles"] == ["H_ALT"]
    assert result.duplicate_groups_found == 2
    assert result.duplicate_links_repaired == 2
    assert result.duplicate_groups_remaining == 0
    assert result.integrity_passed is True


def test_integrity_does_not_guess_when_alternative_is_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    working = root / "working"
    _write(
        working / "approved_geometry_links.json",
        {
            "project_name": "Project",
            "links": [
                _link("A1", "2N10x100", "G", layer="Arm - N10", length=100.0),
                _link("A2", "2N10x100", "G", layer="Arm - N10", length=100.0),
            ],
            "history": [],
        },
    )
    _write(working / "review_center_state.json", {"decisions": {}})
    _write(
        working / "extracted_entities.json",
        {
            "entities": [
                _annotation("A1", (20.0, 5.0)),
                _annotation("A2", (20.0, 20.0)),
                _line("G", "Arm - N10", (0.0, 0.0), (100.0, 0.0)),
                _line("G2", "Arm - N10", (0.0, 15.0), (100.0, 15.0)),
                _line("G3", "Arm - N10", (0.0, 25.0), (100.0, 25.0)),
            ]
        },
    )
    report = tmp_path / "report.json"
    _write(report, {"rows": [_report_row("A1", "2N10x100"), _report_row("A2", "2N10x100")]})

    result = reconcile_project_integrity(root, report_path=report, apply=True)

    assert result.duplicate_links_repaired == 0
    assert result.duplicate_links_removed == 1
    assert result.unresolved_positions == 1
    assert result.integrity_passed is False


def test_integrity_preserves_shared_position_key_legend_references(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    working = root / "working"
    links = [
        _link("A1", "12N8x360", "LEGEND", layer="ARM-GORNA", length=360.0),
        _link("A2", "48N8x360", "LEGEND", layer="ARM-GORNA", length=360.0),
    ]
    for link in links:
        link["candidate_basis"] = "ЕДИНИЧЕН DXF ОБЕКТ; POSITION_KEY_LEGEND_REFERENCE"
        link["candidate_notes"] = [
            "Геометрията LEGEND е легендна референция, а не локална собственост."
        ]
    _write(
        working / "approved_geometry_links.json",
        {"project_name": "Project", "links": links, "history": []},
    )
    _write(working / "review_center_state.json", {"decisions": {}})
    _write(
        working / "extracted_entities.json",
        {
            "entities": [
                _annotation("A1", (10.0, 10.0)),
                _annotation("A2", (20.0, 20.0)),
                _line("LEGEND", "ARM-GORNA", (0.0, 0.0), (360.0, 0.0)),
            ]
        },
    )
    report = tmp_path / "report.json"
    _write(report, {"rows": [_report_row("A1", "12N8x360"), _report_row("A2", "48N8x360")]})

    result = reconcile_project_integrity(root, report_path=report, apply=True)

    approved = json.loads((working / "approved_geometry_links.json").read_text(encoding="utf-8"))
    assert {row["annotation_handle"] for row in approved["links"]} == {"A1", "A2"}
    assert result.duplicate_groups_found == 0
    assert result.duplicate_links_removed == 0
    assert result.approved_after == 2
    assert result.integrity_passed is True


def _auto_link_for_report(
    *, annotation: str, geometry: str, report: Path, report_name: str | None = None
) -> dict:
    return {
        "annotation_handle": annotation,
        "annotation_text": "50N8x105",
        "geometry_handles": [geometry],
        "geometry_layer": "Stremena",
        "master_shape_code": "F006",
        "approved_by": "AUTO_BATCH_GATE",
        "approval_status": "ОДОБРЕНО АВТОМАТИЧНО (ПАКЕТНА ПОРТА)",
        "auto_gate": {
            "report": report_name or report.name,
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "matcher_disposition": "MANUAL",
        },
        "source_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        "source_dxf_sha256": "source-hash",
        "engine_hash": "engine-hash",
        "matcher_sha256": "matcher-hash",
        "rules_sha256": "rules-hash",
    }


def _prepare_valid_auto_report_case(
    *,
    root: Path,
    report: Path,
    recorded_report_name: str,
    source_report_sha256: str | None = None,
) -> None:
    working = root / "working"
    row = _report_row("A1::BJ-FI-L", "50N8x105")
    row.update(
        {
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "matcher_disposition": "MANUAL",
            "candidate_handles": ["G1"],
            "master_shape_code": "F006",
            **_valid_f006_production_contract(),
        }
    )
    _write(report, {"rows": [row]})
    link = _auto_link_for_report(
        annotation="A1::BJ-FI-L",
        geometry="G1",
        report=report,
        report_name=recorded_report_name,
    )
    if source_report_sha256 is not None:
        link["source_report_sha256"] = source_report_sha256
    _write(
        working / "approved_geometry_links.json",
        {"project_name": "Project", "links": [link], "history": []},
    )
    _write(working / "review_center_state.json", {"decisions": {}})
    _write(
        working / "extracted_entities.json",
        {
            "entities": [
                _annotation("A1::BJ-FI-L", (10.0, 10.0)),
                _line("G1", "Stremena", (0.0, 0.0), (105.0, 0.0)),
            ]
        },
    )


def test_integrity_accepts_canonical_staged_report_name_change_only_with_matching_hash(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    report = root / "working" / "final_control_sources" / "batch_report.json"
    _prepare_valid_auto_report_case(
        root=root,
        report=report,
        recorded_report_name="Project_geometry_batch.json",
    )

    result = reconcile_project_integrity(root, report_path=report, apply=True)

    assert result.stale_approvals_revoked == 0
    assert result.approved_after == 1
    assert result.integrity_passed is True
    assert not any(
        issue.details.get("reason") == "AUTO_LINK_REPORT_NAME_MISMATCH"
        for issue in result.issues
    )


def test_integrity_rejects_canonical_staged_name_change_when_hash_mismatches(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    report = root / "working" / "final_control_sources" / "batch_report.json"
    _prepare_valid_auto_report_case(
        root=root,
        report=report,
        recorded_report_name="Project_geometry_batch.json",
        source_report_sha256="0" * 64,
    )

    result = reconcile_project_integrity(root, report_path=report, apply=True)

    assert result.stale_approvals_revoked == 1
    assert result.approved_after == 0
    assert result.integrity_passed is False
    assert any(
        issue.details.get("reason") == "AUTO_LINK_REPORT_HASH_MISMATCH"
        for issue in result.issues
    )


def test_integrity_rejects_report_name_change_outside_canonical_stage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    # Even the canonical basename is insufficient outside the exact
    # project-local staging path.
    report = tmp_path / "external" / "batch_report.json"
    _prepare_valid_auto_report_case(
        root=root,
        report=report,
        recorded_report_name="Project_geometry_batch.json",
    )

    result = reconcile_project_integrity(root, report_path=report, apply=True)

    assert result.stale_approvals_revoked == 1
    assert result.approved_after == 0
    assert result.integrity_passed is False
    assert any(
        issue.details.get("reason") == "AUTO_LINK_REPORT_NAME_MISMATCH"
        for issue in result.issues
    )


def test_integrity_keeps_deterministic_auto_link_that_passed_production_gate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    working = root / "working"
    report = tmp_path / "report.json"
    row = _report_row("A1::BJ-FI-L", "50N8x105")
    row.update(
        {
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "matcher_disposition": "MANUAL",
            "candidate_handles": ["G1"],
            "master_shape_code": "F006",
            **_valid_f006_production_contract(),
        }
    )
    _write(report, {"rows": [row]})
    _write(
        working / "approved_geometry_links.json",
        {
            "project_name": "Project",
            "links": [
                _auto_link_for_report(
                    annotation="A1::BJ-FI-L", geometry="G1", report=report
                )
            ],
            "history": [],
        },
    )
    _write(working / "review_center_state.json", {"decisions": {}})
    _write(
        working / "extracted_entities.json",
        {
            "entities": [
                _annotation("A1::BJ-FI-L", (10.0, 10.0)),
                _line("G1", "Stremena", (0.0, 0.0), (105.0, 0.0)),
            ]
        },
    )

    result = reconcile_project_integrity(root, report_path=report, apply=True)

    assert result.stale_approvals_revoked == 0
    assert result.approved_after == 1
    assert result.unresolved_positions == 0
    assert result.integrity_passed is True


def test_integrity_revokes_auto_link_when_current_report_hash_changed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    working = root / "working"
    report = tmp_path / "report.json"
    row = _report_row("A1", "2N10x100")
    row["batch_status"] = "СИЛЕН"
    row["matcher_disposition"] = "AUTO"
    row["candidate_handles"] = ["G1"]
    _write(report, {"rows": [row]})
    link = _auto_link_for_report(annotation="A1", geometry="G1", report=report)
    link["source_report_sha256"] = "0" * 64
    _write(
        working / "approved_geometry_links.json",
        {"project_name": "Project", "links": [link], "history": []},
    )
    _write(working / "review_center_state.json", {"decisions": {}})
    _write(
        working / "extracted_entities.json",
        {
            "entities": [
                _annotation("A1", (10.0, 10.0)),
                _line("G1", "Stremena", (0.0, 0.0), (100.0, 0.0)),
            ]
        },
    )

    result = reconcile_project_integrity(root, report_path=report, apply=True)

    assert result.stale_approvals_revoked == 1
    assert any(
        issue.details.get("reason") == "AUTO_LINK_REPORT_HASH_MISMATCH"
        for issue in result.issues
    )


def test_engineer_link_resolves_original_missing_master_and_completes_project(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    working = root / "working"
    _write(
        working / "approved_geometry_links.json",
        {
            "project_name": "Project",
            "links": [
                {
                    "annotation_handle": "A1",
                    "source_annotation_text": "60N8/s=20",
                    "resolved_annotation_text": "60N8x94",
                    "annotation_text": "60N8x94",
                    "annotation_count": 60,
                    "diameter_mm": 8.0,
                    "production_length_cm": 94.0,
                    "production_segment_lengths_cm": [40.0, 14.0, 40.0],
                    "geometry_handles": [],
                    "geometry_optional_by_engineer": True,
                    "master_shape_code": "F005",
                    "master_shape_name": "ФИБА",
                    "approved_by": "OWNER_ENGINEER_DECISION",
                    "approval_status": "ENGINEER_RESOLVED",
                    "engineer_decision": {"action_class": "OVERRIDE", "reason_code": "NO_GEOMETRY"},
                }
            ],
            "history": [],
        },
    )
    _write(
        working / "review_center_state.json",
        {"decisions": {"A1": {"decision": "ENGINEER_RESOLVED", "action_class": "OVERRIDE"}}},
    )
    _write(working / "extracted_entities.json", {"entities": [_annotation("A1", (10.0, 10.0))]})
    report = tmp_path / "report.json"
    row = _report_row("A1", "60N8/s=20")
    row.update(
        {
            "annotation_count": 60,
            "diameter_mm": 8.0,
            "declared_length_cm": None,
            "batch_status": "ЛИПСВА MASTER ФОРМА",
            "candidate_handles": ["BAD"],
            "master_shape_code": None,
        }
    )
    _write(report, {"rows": [row]})

    result = reconcile_project_integrity(root, report_path=report, apply=True)

    assert result.approved_after == 1
    assert result.intrinsic_missing_master == 0
    assert result.unresolved_positions == 0
    assert result.integrity_passed is True
    assert result.full_specification_complete is True


def test_explicit_exclusion_is_accounted_but_blocks_full_production_export(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    working = root / "working"
    _write(
        working / "approved_geometry_links.json",
        {"project_name": "Project", "links": [], "history": []},
    )
    _write(
        working / "review_center_state.json",
        {
            "decisions": {
                "A1": {
                    "decision": "EXCLUDED_BY_ENGINEER",
                    "action_class": "EXCLUSION",
                    "reason_code": "PROJECTANT_ERROR",
                }
            }
        },
    )
    report = tmp_path / "report.json"
    _write(report, {"rows": [_report_row("A1", "2N10x100")]})

    result = reconcile_project_integrity(root, report_path=report, apply=True)

    assert result.explicit_exclusions == 1
    assert result.accounted_positions == 1
    assert result.unresolved_positions == 0
    assert result.integrity_passed is True
    assert result.full_specification_complete is False
