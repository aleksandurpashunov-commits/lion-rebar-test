from __future__ import annotations

import csv
import json
from pathlib import Path

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.export.excel_export import load_export_context
from lion_rebar.project.models import ProjectModel


def test_only_final_approved_rows_enter_export(tmp_path: Path) -> None:
    working = tmp_path / "working"
    working.mkdir()
    (working / "final_integrity_report.json").write_text(json.dumps({"integrity_passed": True, "full_specification_complete": True, "duplicate_groups_remaining": 0}), encoding="utf-8")
    with (working / "final_integrity_ledger.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["annotation_handle", "annotation_text", "final_status", "shape_code", "shape_name", "production_length_cm", "declared_length_cm", "production_length_source"])
        writer.writeheader()
        writer.writerow({"annotation_handle": "T1", "annotation_text": "2N10x100", "final_status": "APPROVED", "shape_code": "F001", "shape_name": "ПРАВ ПРЪТ", "production_length_cm": "100"})
        writer.writerow({"annotation_handle": "T2", "annotation_text": "5N10x200", "final_status": "MISSING_MASTER", "production_length_cm": "200"})
        writer.writerow({"annotation_handle": "T3", "annotation_text": "6N10x300", "final_status": "NO_VALID_CANDIDATE", "production_length_cm": "300"})
    (working / "approved_geometry_links.json").write_text(json.dumps({"links": [{"annotation_handle": "T1", "geometry_handles": ["G1"], "approval_status": "APPROVED"}]}), encoding="utf-8")
    project = ProjectModel(name="P", root=str(tmp_path))
    entities = [
        DxfEntityRecord("C", "TEXT", "$Ramka", {"insert": [0, 0, 0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord("TITLE", "TEXT", "Layer1", {"insert": [100, 0, 0]}, "ДОЛНА АРМИРОВКА"),
        DxfEntityRecord("T1", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [-100, 500, 0]}, "2N10x100"),
        DxfEntityRecord("G1", "LINE", "Arm - N10", {"start": [-100, 500, 0], "end": [0, 500, 0]}),
    ]
    engine = EnginePackage(engine_id="E", title="E", version="1", shapes=[ShapeDefinition(code="F001", name="ПРАВ ПРЪТ", active=True, segments=[SegmentDefinition(index=1, length_code="L1")])])
    frames, positions = load_export_context(project, entities, engine)
    assert len(frames) == 1
    assert [position.annotation_handle for position in positions] == ["T1"]


def test_engineer_resolved_annotation_only_position_enters_export(tmp_path: Path) -> None:
    working = tmp_path / "working"
    working.mkdir()
    (working / "final_integrity_report.json").write_text(
        json.dumps(
            {
                "integrity_passed": True,
                "full_specification_complete": True,
                "duplicate_groups_remaining": 0,
            }
        ),
        encoding="utf-8",
    )
    with (working / "final_integrity_ledger.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "annotation_handle",
                "original_annotation_text",
                "resolved_annotation_text",
                "final_status",
                "shape_code",
                "shape_name",
                "production_count",
                "production_diameter_mm",
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
                "production_count": "60",
                "production_diameter_mm": "8",
                "production_length_cm": "94",
                "production_length_source": "OWNER_ENGINEER_DECISION",
            }
        )
    (working / "approved_geometry_links.json").write_text(
        json.dumps(
            {
                "links": [
                    {
                        "annotation_handle": "T1",
                        "source_annotation_text": "60N8/s=20",
                        "resolved_annotation_text": "60N8x94",
                        "annotation_text": "60N8x94",
                        "production_count": 60,
                        "production_diameter_mm": 8,
                        "production_length_cm": 94,
                        "production_segment_lengths_cm": [40, 14, 40],
                        "geometry_handles": [],
                        "geometry_optional_by_engineer": True,
                        "master_shape_code": "F005",
                        "master_shape_name": "ФИБА",
                        "approval_status": "ENGINEER_RESOLVED",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    project = ProjectModel(name="P", root=str(tmp_path))
    entities = [
        DxfEntityRecord("C", "TEXT", "$Ramka", {"insert": [0, 0, 0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord("TITLE", "TEXT", "Layer1", {"insert": [100, 0, 0]}, "ДОЛНА АРМИРОВКА"),
        DxfEntityRecord("T1", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [-100, 500, 0]}, "60N8/s=20"),
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
                revision=3,
                segments=[
                    SegmentDefinition(index=1, length_code="L1"),
                    SegmentDefinition(index=2, length_code="L2"),
                    SegmentDefinition(index=3, length_code="L3"),
                ],
            )
        ],
    )

    _frames, positions = load_export_context(project, entities, engine)
    assert len(positions) == 1
    position = positions[0]
    assert position.annotation_text == "60N8x94"
    assert position.count == 60
    assert position.diameter_mm == 8
    assert position.production_length_cm == 94
    assert position.segment_lengths_cm == (40.0, 14.0, 40.0)
    assert position.geometry_handles == ()
