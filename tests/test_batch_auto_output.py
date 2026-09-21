from __future__ import annotations

import json
from pathlib import Path

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.export.batch_auto_output import build_batch_export_context


def _engine() -> EnginePackage:
    return EnginePackage(
        engine_id="test-engine",
        title="TEST",
        version="0.3.12",
        production_ready=True,
        shapes=[
            ShapeDefinition(
                code="F001",
                name="ПРАВ ПРЪТ",
                state="LOCKED",
                locked=True,
                segments=[SegmentDefinition(index=1, length_code="L1")],
            ),
            ShapeDefinition(
                code="F004",
                name="ПРЪТ С ЕДИН ВИНКЕЛ",
                state="LOCKED",
                locked=True,
                segments=[
                    SegmentDefinition(index=1, length_code="L1"),
                    SegmentDefinition(index=2, length_code="L2"),
                ],
            ),
            ShapeDefinition(code="F006", name="СТРЕМЕ", state="LOCKED", locked=True),
            ShapeDefinition(code="F007", name="C-ФОРМА", state="LOCKED", locked=True),
            ShapeDefinition(code="F013", name="СТЪЛБИЩНО РАМО 2", state="LOCKED", locked=True),
        ],
    )


def _frame_entities() -> list[DxfEntityRecord]:
    return [
        DxfEntityRecord("FRAME", "TEXT", "$Ramka", {"insert": [0, 0, 0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord("TITLE", "TEXT", "0", {"insert": [100, 0, 0]}, "АРМИРОВКА ГРЕДИ"),
        DxfEntityRecord("BEAM", "TEXT", "0", {"insert": [100, 750, 0]}, "Гр. B.1 25/50"),
    ]


def _row(handle: str, text: str, *, code: str, count: int, length: float, handles: list[str]) -> dict:
    return {
        "annotation_handle": handle,
        "annotation_text": text,
        "annotation_class": "ACTIONABLE",
        "annotation_count": count,
        "diameter_mm": 8.0,
        "declared_length_cm": length,
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "matcher_disposition": "CONTROL",
        "global_conflict": False,
        "data_quality_flags": [],
        "quantity_review_required": False,
        "master_shape_code": code,
        "candidate_handles": handles,
        "candidate_notes": [],
    }


def test_batch_context_exports_only_master_verified_rows(tmp_path: Path) -> None:
    entities = _frame_entities() + [
        DxfEntityRecord("T1", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [100, 500, 0]}, "2N8x200"),
        DxfEntityRecord("G1", "LINE", "Arm - N8", {"start": [100, 500, 0], "end": [300, 500, 0]}),
        DxfEntityRecord("T2", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [400, 500, 0]}, "2N8x270"),
        DxfEntityRecord("G2", "LINE", "Arm - N8", {"start": [400, 500, 0], "end": [650, 500, 0]}),
        DxfEntityRecord("G3", "LINE", "Arm - N8", {"start": [650, 500, 0], "end": [650, 530, 0]}),
        DxfEntityRecord("T3", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [700, 500, 0]}, "2N8x435"),
        DxfEntityRecord("G4", "LINE", "Arm - N8", {"start": [700, 500, 0], "end": [1105, 500, 0]}),
        DxfEntityRecord("G5", "LINE", "Arm - N8", {"start": [1105, 500, 0], "end": [1135, 515, 0]}),
        DxfEntityRecord("T4", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [1200, 500, 0]}, "2N8x280"),
        DxfEntityRecord("G6", "LINE", "Arm - N8", {"start": [1200, 500, 0], "end": [1450, 500, 0]}),
        DxfEntityRecord("G7", "LINE", "Arm - N8", {"start": [1450, 500, 0], "end": [1450, 540, 0]}),
    ]
    safe = _row("T1", "2N8x200", code="F001", count=2, length=200, handles=["G1"])
    manual = _row("T2", "2N8x270", code="F004", count=2, length=270, handles=["G2", "G3"])
    manual["matcher_disposition"] = "MANUAL"
    stair = _row("T3", "2N8x435", code="F013", count=2, length=435, handles=["G4", "G5"])
    bad_total = _row("T4", "2N8x280", code="F004", count=2, length=280, handles=["G6", "G7"])
    bad_total["canonical_segment_lengths_cm"] = [250.0, 40.0]

    report = tmp_path / "batch.json"
    report.write_text(
        json.dumps({"cancelled": False, "rows": [safe, manual, stair, bad_total]}, ensure_ascii=False),
        encoding="utf-8",
    )
    context = build_batch_export_context(report, entities=entities, engine=_engine())

    assert context.actionable_count == 4
    assert [position.annotation_handle for position in context.positions] == ["T1"]
    reasons = {blocker.annotation_handle: blocker.reason_codes for blocker in context.blockers}
    assert "MATCHER_REVIEW" in reasons["T2"]
    assert "BEAM_STAIR_SHAPE" in reasons["T3"]
    assert "MISSING_MASTER_RENDERER" in reasons["T3"]
    assert "MASTER_LENGTH_CHECK" in reasons["T4"]
    assert context.complete is False


def test_operator_frame_decision_controls_automatic_batch_context(tmp_path: Path) -> None:
    entities = [
        DxfEntityRecord("FRAME", "TEXT", "$Ramka", {"insert": [0, 0, 0]},
                        "ЧЕРТЕЖ:"),
        DxfEntityRecord("T1", "BLOCK_ATTR_TEXT", "ARM_TXT",
                        {"insert": [100, 500, 0]}, "2N8x100"),
        DxfEntityRecord("G1", "LINE", "ARM_Bars",
                        {"start": [100, 500, 0], "end": [200, 500, 0]}),
    ]
    row = _row("T1", "2N8x100", code="F001", count=2,
               length=100, handles=["G1"])
    report = tmp_path / "batch.json"
    report.write_text(json.dumps({"cancelled": False, "rows": [row]}),
                      encoding="utf-8")

    context = build_batch_export_context(
        report, entities=entities, engine=_engine(),
        frame_decisions={"FRAME": "VERTICALS"},
    )

    assert context.frames[0].category_code == "VERTICALS"
    assert context.frames[0].resolved_by == "OPERATOR"
    assert context.positions[0].frame_index == context.frames[0].index


def test_f006_uses_printed_dimensions_not_schematic_dxf_length(tmp_path: Path) -> None:
    entities = _frame_entities() + [
        DxfEntityRecord("T1", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [100, 500, 0]}, "19N8x160"),
        DxfEntityRecord("R1", "LINE", "ARM_Bars", {"start": [100, 500, 0], "end": [194, 500, 0]}),
        DxfEntityRecord("R2", "LINE", "ARM_Bars", {"start": [194, 500, 0], "end": [194, 534, 0]}),
        DxfEntityRecord("R3", "LINE", "ARM_Bars", {"start": [194, 534, 0], "end": [100, 534, 0]}),
        DxfEntityRecord("R4", "LINE", "ARM_Bars", {"start": [100, 534, 0], "end": [100, 500, 0]}),
        DxfEntityRecord("H1", "LINE", "ARM_Bars", {"start": [180, 534, 0], "end": [194, 548, 0]}),
        DxfEntityRecord("H2", "LINE", "ARM_Bars", {"start": [194, 525, 0], "end": [180, 511, 0]}),
        DxfEntityRecord("DW", "TEXT", "koti", {"insert": [147, 492, 0], "rotation": 0}, "50"),
        DxfEntityRecord("DH", "TEXT", "koti", {"insert": [92, 517, 0], "rotation": 90}, "20"),
        DxfEntityRecord("DK", "TEXT", "koti", {"insert": [198, 541, 0], "rotation": 45}, "10"),
    ]
    row = _row(
        "T1",
        "19N8x160",
        code="F006",
        count=19,
        length=160,
        handles=["R1", "R2", "R3", "R4", "H1", "H2"],
    )
    row["batch_status"] = "СИЛНО СТРЕМЕ F006"
    row["canonical_segment_lengths_cm"] = [34.0, 94.0, 34.0, 94.0, 20.0, 20.0]

    report = tmp_path / "batch.json"
    report.write_text(json.dumps({"cancelled": False, "rows": [row]}, ensure_ascii=False), encoding="utf-8")
    context = build_batch_export_context(report, entities=entities, engine=_engine())

    assert context.complete is True
    assert len(context.positions) == 1
    assert context.positions[0].segment_lengths_cm == (10.0, 50.0, 20.0, 50.0, 20.0, 10.0)
    assert sum(context.positions[0].segment_lengths_cm) == 160.0


def test_invalid_derived_f006_cannot_bypass_engine_length_gate(tmp_path: Path) -> None:
    handle = "T1::BJ-FI-L"
    entities = _frame_entities() + [
        DxfEntityRecord(handle, "BLOCK_ATTR_TEXT", "ARM_TXT",
                        {"insert": [100, 500, 0]}, "25N8x135"),
        DxfEntityRecord("G1", "LINE", "ARM_Bars",
                        {"start": [100, 500, 0], "end": [200, 500, 0]}),
        DxfEntityRecord("SAFE", "BLOCK_ATTR_TEXT", "ARM_TXT",
                        {"insert": [300, 500, 0]}, "2N8x100"),
        DxfEntityRecord("G2", "LINE", "ARM_Bars",
                        {"start": [300, 500, 0], "end": [400, 500, 0]}),
    ]
    row = _row(handle, "25N8x135", code="F006", count=25,
               length=135, handles=["G1"])
    row["f006_production_status"] = "F006_PRODUCTION_DERIVED"
    row["dimension_derived_evidence"] = {"source": "OWNED_DIMENSIONS"}
    row["production_segments_cm"] = [10, 50, 100, 50, 100, 10]
    safe = _row("SAFE", "2N8x100", code="F001", count=2,
                length=100, handles=["G2"])
    report = tmp_path / "batch.json"
    report.write_text(json.dumps({"cancelled": False, "rows": [row, safe]}),
                      encoding="utf-8")

    context = build_batch_export_context(report, entities=entities, engine=_engine())

    assert [position.annotation_handle for position in context.positions] == ["SAFE"]
    blocker = next(item for item in context.blockers
                   if item.annotation_handle == handle)
    assert "MASTER_LENGTH_CHECK" in blocker.reason_codes


def test_truthy_production_segment_evidence_cannot_bypass_batch_and_matcher(
    tmp_path: Path,
) -> None:
    forged_handle = "FORGED::BJ-FI-L"
    entities = _frame_entities() + [
        DxfEntityRecord(
            forged_handle,
            "BLOCK_ATTR_TEXT",
            "ARM_TXT",
            {"insert": [100, 500, 0]},
            "1N8x40",
        ),
        DxfEntityRecord(
            "G-FORGED",
            "LINE",
            "ARM_Bars",
            {"start": [100, 500, 0], "end": [140, 500, 0]},
        ),
        DxfEntityRecord(
            "SAFE",
            "BLOCK_ATTR_TEXT",
            "ARM_TXT",
            {"insert": [300, 500, 0]},
            "2N8x100",
        ),
        DxfEntityRecord(
            "G-SAFE",
            "LINE",
            "ARM_Bars",
            {"start": [300, 500, 0], "end": [400, 500, 0]},
        ),
    ]
    forged = _row(
        forged_handle,
        "1N8x40",
        code="F007",
        count=1,
        length=40,
        handles=["G-FORGED"],
    )
    forged.update(
        {
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "matcher_disposition": "MANUAL",
            "production_segments_cm": [10.0, 20.0, 10.0],
            "production_segment_evidence": {"source": "TRUTHY"},
        }
    )
    safe = _row(
        "SAFE",
        "2N8x100",
        code="F001",
        count=2,
        length=100,
        handles=["G-SAFE"],
    )
    report = tmp_path / "batch.json"
    report.write_text(
        json.dumps({"cancelled": False, "rows": [forged, safe]}),
        encoding="utf-8",
    )

    context = build_batch_export_context(report, entities=entities, engine=_engine())

    assert [position.annotation_handle for position in context.positions] == ["SAFE"]
    blocker = next(
        item for item in context.blockers if item.annotation_handle == forged_handle
    )
    assert "BATCH_STATUS" in blocker.reason_codes
    assert "MATCHER_REVIEW" in blocker.reason_codes


def test_project_approved_position_is_retained_in_next_automatic_excel(tmp_path: Path) -> None:
    entities = _frame_entities() + [
        DxfEntityRecord("T1", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [100, 500, 0]}, "2N8x200"),
        DxfEntityRecord("G1", "LINE", "Arm - N8", {"start": [100, 500, 0], "end": [300, 500, 0]}),
    ]
    row = _row("T1", "2N8x200", code="F001", count=2, length=200, handles=["G1"])
    # A skipped/previously resolved OWNER row may not be restamped as
    # APPROVED_IN_PROJECT by the fresh batch. The supplied validated manual
    # link remains the authority.
    row["batch_status"] = "ЗА РЪЧЕН ПРЕГЛЕД"
    row["matcher_disposition"] = "MANUAL"
    approved_link = {
        "annotation_handle": "T1",
        "annotation_text": "2N8x200",
        "diameter_mm": 8.0,
        "declared_length_cm": 200.0,
        "geometry_handles": ["G1"],
        "master_shape_code": "F001",
        "approval_status": "ОДОБРЕНО РЪЧНО",
    }
    report = tmp_path / "batch.json"
    report.write_text(json.dumps({"cancelled": False, "rows": [row]}, ensure_ascii=False), encoding="utf-8")

    context = build_batch_export_context(
        report,
        entities=entities,
        engine=_engine(),
        approved_links={"T1": approved_link},
    )

    assert context.complete is True
    assert len(context.positions) == 1
    assert context.positions[0].approval_status == "ОДОБРЕНО РЪЧНО"
    assert context.positions[0].segment_lengths_cm == (200.0,)


def test_generic_production_block_keeps_its_real_reason(tmp_path: Path) -> None:
    entities = _frame_entities() + [
        DxfEntityRecord(
            "T1",
            "BLOCK_ATTR_TEXT",
            "ARM_TXT",
            {"insert": [100, 500, 0]},
            "2N8x120",
        ),
        DxfEntityRecord(
            "G1",
            "LINE",
            "Arm - N8",
            {"start": [100, 500, 0], "end": [200, 500, 0]},
        ),
        DxfEntityRecord(
            "G2",
            "LINE",
            "Arm - N8",
            {"start": [200, 500, 0], "end": [200, 520, 0]},
        ),
        DxfEntityRecord(
            "SAFE",
            "BLOCK_ATTR_TEXT",
            "ARM_TXT",
            {"insert": [300, 500, 0]},
            "2N8x100",
        ),
        DxfEntityRecord(
            "G3",
            "LINE",
            "Arm - N8",
            {"start": [300, 500, 0], "end": [400, 500, 0]},
        ),
    ]
    blocked = _row(
        "T1", "2N8x120", code="F004", count=2, length=120, handles=["G1", "G2"]
    )
    blocked["production_export_blocked"] = True
    blocked["production_export_block_reason"] = "AUTHORITATIVE_DIMENSION_SHAPE_INVALID"
    safe = _row("SAFE", "2N8x100", code="F001", count=2, length=100, handles=["G3"])
    report = tmp_path / "batch.json"
    report.write_text(
        json.dumps({"cancelled": False, "rows": [blocked, safe]}),
        encoding="utf-8",
    )

    context = build_batch_export_context(report, entities=entities, engine=_engine())

    blocker = next(item for item in context.blockers if item.annotation_handle == "T1")
    assert "AUTHORITATIVE_DIMENSION_SHAPE_INVALID" in blocker.reason_codes
    assert "F006_PRODUCTION_REVIEW" not in blocker.reason_codes


def test_неопределената_рамка_блокира_само_своите_позиции(tmp_path: Path) -> None:
    """Собственик, 20.09.2026: една рамка без категория да не спира целия чертеж.

    Дотогава ``build_batch_export_context`` вдигаше грешка за ВСИЧКО, щом има
    поне една неопределена рамка. Измерено: „KOFR_B“ има 43 листа, от които 6 с
    разпозната категория, и въпреки това нула от 2974 позиции минаваха;
    „S-SR+27,82“ намира вярно ГОРНА и ДОЛНА, но една трета рамка без заглавие
    обезсилваше и двете.

    Финалният контрол остава строг — той пази пълната спецификация.
    """

    entities = [
        # лист 1: щемпел със заглавие -> ДОЛНА
        DxfEntityRecord("FRAME1", "TEXT", "$Ramka", {"insert": [0, 0, 0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord("TITLE1", "TEXT", "0", {"insert": [100, 0, 0]}, "ДОЛНА АРМИРОВКА"),
        DxfEntityRecord("T1", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [100, 500, 0]}, "2N8x100"),
        DxfEntityRecord("G1", "LINE", "ARM_Bars", {"start": [100, 500, 0], "end": [200, 500, 0]}),
        # лист 2: щемпел БЕЗ заглавие -> неопределена категория
        DxfEntityRecord("FRAME2", "TEXT", "$Ramka", {"insert": [10000, 0, 0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord("T2", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [10100, 500, 0]}, "3N8x120"),
        DxfEntityRecord("G2", "LINE", "ARM_Bars", {"start": [10100, 500, 0], "end": [10220, 500, 0]}),
    ]
    rows = [
        _row("T1", "2N8x100", code="F001", count=2, length=100, handles=["G1"]),
        _row("T2", "3N8x120", code="F001", count=3, length=120, handles=["G2"]),
    ]
    report = tmp_path / "batch.json"
    report.write_text(json.dumps({"cancelled": False, "rows": rows}), encoding="utf-8")

    context = build_batch_export_context(report, entities=entities, engine=_engine())

    # позицията в разпознатия лист минава
    assert [position.annotation_handle for position in context.positions] == ["T1"]
    # позицията в неопределения лист е отказана, и то с точната причина
    blocker = next(item for item in context.blockers if item.annotation_handle == "T2")
    assert "UNKNOWN_FRAME" in blocker.reason_codes
    # и лист „неопределен“ не се прави: празен лист изглежда като готова заявка
    assert [config.category_code for config in context.frame_configs] == ["BOTTOM"]
    assert context.actionable_count == 2
    assert not context.complete
